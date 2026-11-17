FROM python:3.12-slim AS build
WORKDIR /build
RUN pip install --no-cache-dir uv==0.13.5
COPY pyproject.toml uv.lock ./
COPY agent/pyproject.toml agent/README.md agent/LICENSE agent/hatch_build.py agent/
COPY agent/src agent/src
COPY web web
RUN uv export --frozen --no-dev --no-emit-workspace --package omnioperator --output-file /requirements.txt
RUN uv build --package omnioperator --wheel --out-dir /wheels

FROM python:3.12-slim
RUN groupadd --gid 10001 omni && useradd --uid 10001 --gid omni --create-home omni \
    && install -d -m 0700 -o omni -g omni /home/omni/.local /home/omni/.local/share /home/omni/.local/share/OmniOperator /home/omni/.local/share/OmniOperator/v2
COPY --from=build /requirements.txt /requirements.txt
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --require-hashes -r /requirements.txt && pip install --no-cache-dir --no-deps /wheels/*.whl
USER 10001:10001
ENV HOME=/home/omni OMNI_HOME=/home/omni/.local/share/OmniOperator/v2 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
VOLUME ["/home/omni/.local/share/OmniOperator/v2"]
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8766/health/ready',timeout=3)"
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "omni.composition.cli"]
CMD ["serve", "--host", "127.0.0.1", "--stdout-logs"]
