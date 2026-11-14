"""CPU ONNX/tokenizers subprocess. No database, credentials, model downloads or arbitrary Python imports."""

from __future__ import annotations

import importlib.metadata
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from ...domain.contracts import BusinessError
from ...domain.local_vectors import (
    MAX_FRAME,
    PROTOCOL,
    canonical,
    decode,
    invalid,
    texts_request,
    vectors,
    version,
)
from ..vector_files import identity, read_file, read_manifest, runtime_versions

Payload = dict[str, object]


def no_external_tensors(message: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    remaining = [200000] if budget is None else budget
    remaining[0] -= 1
    if depth > 64 or remaining[0] < 0:
        raise invalid("vector_graph_unsupported")
    # Protobuf traversal covers initializers, sparse tensors, attributes and function subgraphs.
    if message.DESCRIPTOR.full_name == "onnx.TensorProto" and (message.external_data or message.data_location != 0):
        raise invalid("vector_external_data_forbidden")
    for field, value in message.ListFields():
        if field.type == field.TYPE_MESSAGE:
            for child in value if field.is_repeated else [value]:
                no_external_tensors(child, depth + 1, remaining)


class Runtime:
    def __init__(self) -> None:
        self.root: Path | None = None
        self.spec: Payload = {}
        self.captured: dict[str, tuple[int, ...]] = {}
        self.version = ""
        self.session: Any = None
        self.tokenizer: Any = None
        self.np: Any = None
        self.inputs: set[str] = set()

    def verify(self) -> None:
        if self.root is None:
            raise invalid("vector_not_loaded")
        for name, captured in self.captured.items():
            if identity(self.root / name) != captured:
                raise invalid("vector_bundle_changed")

    def load(self, arguments: Payload) -> Payload:
        if self.root is not None or set(arguments) != {"bundle", "sha256", "version"}:
            raise invalid("vector_protocol_invalid")
        if any(importlib.metadata.version(name) != wanted for name, wanted in runtime_versions().items()):
            raise invalid("vector_runtime_mismatch")
        root = Path(cast(str, arguments["bundle"]))
        spec, captured = read_manifest(root, cast(str, arguments["sha256"]))
        if version(spec, cast(Payload, runtime_versions())) != arguments["version"]:
            raise invalid("vector_protocol_invalid")
        contents: dict[str, bytes] = {}
        self.captured = {"manifest.json": captured}
        for name, row in cast(dict[str, Payload], spec["files"]).items():
            data, captured = read_file(root / name, cast(int, row["bytes"]), cast(str, row["sha256"]))
            if len(data) != row["bytes"]:
                raise invalid("vector_bundle_changed")
            contents[name] = data
            self.captured[name] = captured
        np = import_module("numpy")
        onnx = import_module("onnx")
        ort = import_module("onnxruntime")
        Tokenizer = import_module("tokenizers").Tokenizer

        model = onnx.load_model_from_string(contents["model.onnx"])
        no_external_tensors(model)
        onnx.checker.check_model(model)
        del model
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 4
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        session = ort.InferenceSession(contents["model.onnx"], sess_options=options, providers=["CPUExecutionProvider"])
        session.disable_fallback()
        names = {item.name for item in session.get_inputs()}
        if names not in ({"input_ids", "attention_mask"}, {"input_ids", "attention_mask", "token_type_ids"}) or any(
            item.type != "tensor(int64)" or len(item.shape) != 2 for item in session.get_inputs()
        ):
            raise invalid("vector_graph_unsupported")
        outputs = {item.name: item for item in session.get_outputs()}
        output = outputs.get(cast(str, spec["output"]))
        if (
            output is None
            or output.type != "tensor(float)"
            or len(output.shape) != (2 if spec["pooling"] == "pooled" else 3)
        ):
            raise invalid("vector_graph_unsupported")
        tokenizer = Tokenizer.from_str(contents["tokenizer.json"].decode("utf-8"))
        if tokenizer.token_to_id(spec["pad_token"]) != spec["pad_id"]:
            raise invalid("vector_tokenizer_invalid")
        tokenizer.no_padding()
        tokenizer.enable_truncation(max_length=spec["max_tokens"], stride=0)
        self.root, self.spec, self.version = root, spec, version(spec, cast(Payload, runtime_versions()))
        self.session, self.tokenizer, self.np, self.inputs = session, tokenizer, np, names
        self.verify()
        return {"version": self.version, "dimension": spec["dimension"], "state": "loaded"}

    def embed(self, arguments: Payload) -> Payload:
        if set(arguments) != {"texts", "purpose", "version"} or arguments["version"] != self.version:
            raise invalid("vector_protocol_invalid")
        texts = texts_request(cast(list[str], arguments["texts"]), cast(str, arguments["purpose"]))
        self.verify()
        spec, np = self.spec, self.np
        prefix = cast(str, spec["query_prefix"] if arguments["purpose"] == "query" else spec["document_prefix"])
        encoded: list[Any] = self.tokenizer.encode_batch([prefix + text for text in texts])
        windows: list[tuple[int, Any]] = []
        for position, row in enumerate(encoded):
            pieces = [row, *row.overflowing]
            if len(pieces) > 16 or any(not p.ids or len(p.ids) > cast(int, spec["max_tokens"]) for p in pieces):
                raise invalid("vector_input_too_large")
            windows.extend((position, piece) for piece in pieces)
        total = np.zeros((len(texts), spec["dimension"]), dtype=np.float64)
        for offset in range(0, len(windows), 8):
            batch = windows[offset : offset + 8]
            length = max(len(row.ids) for _, row in batch)
            ids = np.full((len(batch), length), spec["pad_id"], dtype=np.int64)
            mask = np.zeros_like(ids)
            types = np.zeros_like(ids)
            for index, (_, row) in enumerate(batch):
                ids[index, : len(row.ids)] = row.ids
                mask[index, : len(row.ids)] = row.attention_mask
                types[index, : len(row.ids)] = row.type_ids
            feed = {
                "input_ids": ids,
                "attention_mask": mask,
                **({"token_type_ids": types} if "token_type_ids" in self.inputs else {}),
            }
            output = self.session.run([spec["output"]], feed)[0]
            wanted = (
                (len(batch), spec["dimension"])
                if spec["pooling"] == "pooled"
                else (len(batch), length, spec["dimension"])
            )
            if output.shape != wanted or not np.isfinite(output).all():
                raise invalid("vector_output_invalid")
            if spec["pooling"] == "mean_masked":
                pooled = (output.astype(np.float64) * mask[:, :, None]).sum(axis=1) / np.maximum(
                    mask.sum(axis=1)[:, None], 1
                )
            elif spec["pooling"] == "cls":
                pooled = output[:, 0, :]
            else:
                pooled = output
            for index, (position, _) in enumerate(batch):
                total[position] += pooled[index] * int(mask[index].sum())
        self.verify()
        try:
            result = vectors(total.tolist(), len(texts), cast(int, spec["dimension"]))
        except BusinessError:
            raise invalid("vector_output_invalid") from None
        return {"version": self.version, "vectors": result}


def serve() -> None:
    runtime = Runtime()
    while frame := sys.stdin.buffer.readline(MAX_FRAME + 1):
        try:
            request = decode(frame)
            if set(request) != {"protocol", "id", "method", "arguments"} or not isinstance(request["arguments"], dict):
                return
            if request["method"] not in {"load", "embed"}:
                return
        except BusinessError:
            return
        try:
            result = (
                runtime.load(cast(Payload, request["arguments"]))
                if request["method"] == "load"
                else runtime.embed(cast(Payload, request["arguments"]))
            )
            error = ""
        except BusinessError as exc:
            result, error = {}, exc.code
        except Exception:  # noqa: BLE001 -- model/library/path exceptions never leave the worker as text
            result, error = {}, "vector_runtime_failed"
        response = canonical({"protocol": PROTOCOL, "id": request["id"], "error": error, "result": result}) + b"\n"
        if len(response) > MAX_FRAME:
            return
        sys.stdout.buffer.write(response)
        sys.stdout.buffer.flush()
        if error:
            return


if __name__ == "__main__":
    serve()
