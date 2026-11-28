/** @typedef {{ id: string, title: string, at: string }} Session */
/** @typedef {{ id: string, name: string, version: number | null }} Artifact */
/** @typedef {{ id: string, role: "user" | "assistant", text: string, at: string, artifacts: Artifact[] }} Message */
/** @typedef {{ id: string, inputId: string, label: string, state: string, at: string }} Run */

const root = "/api/v1";
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value) => typeof value === "string" ? value : "";
const items = (value) => record(value) && Array.isArray(value.items) ? value.items.filter(record) : [];
const nullableText = (value) => value === undefined || value === null ? null : text(value);
const nullableInteger = (value) => Number.isInteger(value) ? value : null;
const nullableBoolean = (value) => typeof value === "boolean" ? value : null;

function artifacts(value) {
  if (!Array.isArray(value)) return [];
  return value.filter(record).flatMap((item) => {
    const id = text(item.artifact_id);
    if (!id) return [];
    return [{ id, name: text(item.name) || "产物", version: Number.isInteger(item.version) ? item.version : null }];
  });
}

export class RequestError extends Error {
  constructor(status) {
    super("请求未完成");
    this.status = status;
  }
}

export class WorkbenchClient {
  #token;
  #agent;
  #mainId;
  #workflow;

  constructor(token) {
    this.#token = token;
  }

  async #request(path, signal, body) {
    const headers = { Authorization: "Bearer " + this.#token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(root + path, {
      method: body === undefined ? "GET" : "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
      headers,
      signal,
      cache: "no-store",
      redirect: "error",
    });
    if (!response.ok) throw new RequestError(response.status);
    return response;
  }

  #scope(path) {
    return "/agents/" + encodeURIComponent(this.#agent) + path;
  }

  async workspace(signal) {
    const agents = items(await (await this.#request("/agents", signal)).json());
    const agent = agents.find((entry) => text(entry.id));
    if (!agent) throw new RequestError(404);
    this.#agent = text(agent.id);
    const [sessions, binding] = await Promise.all([
      this.sessions(signal),
      this.#request(this.#scope("/omniweave/main-session"), signal).then((response) => response.json()),
    ]);
    const mainId = this.#mainId = text(binding.session_id);
    this.#workflow = binding.enabled === true;
    return {
      name: text(agent.name) || "我的 Agent",
      sessions,
      mainId: mainId || (binding.enabled === true ? "" : sessions[0]?.id || ""),
      canStart: binding.enabled !== true,
    };
  }

  /** @returns {Promise<Session[]>} */
  async sessions(signal) {
    const response = await this.#request(this.#scope("/sessions"), signal);
    return items(await response.json()).filter((row) => text(row.id))
      .map((row) => ({ id: row.id, title: text(row.title) || "未命名会话", at: text(row.updated_at) || text(row.created_at) }));
  }

  /** @returns {Promise<Message[]>} */
  async messages(session, signal) {
    if (!session) return [];
    const response = await this.#request(this.#scope("/sessions/" + encodeURIComponent(session) + "/messages"), signal);
    return items(await response.json()).filter((row) => row.role === "user" || row.role === "assistant")
      .map((row) => ({
        id: text(row.id),
        role: row.role,
        text: text(row.content),
        at: text(row.created_at),
        artifacts: artifacts(row.omniweave_delivery?.artifacts),
      }));
  }

  async artifact(session, artifactId, signal) {
    const path = "/omniweave/sessions/" + encodeURIComponent(session) + "/artifacts/" + encodeURIComponent(artifactId) + "/content";
    return (await this.#request(this.#scope(path), signal)).text();
  }

  /** @returns {Promise<Run[]>} */
  async runs(session, signal) {
    if (!session) return [];
    const response = await this.#request(this.#scope("/runs"), signal);
    return items(await response.json()).filter((row) => row.session_id === session && text(row.id))
      .map((row) => ({
        id: row.id, inputId: text(row.input_message_id), label: text(row.title), state: text(row.state),
        at: text(row.ended_at) || text(row.created_at),
      }));
  }

  async send(session, message, key, signal) {
    const response = await this.#request(this.#scope("/turns"), signal, { session_id: session || null, key, message });
    const receipt = await response.json();
    if (!text(receipt.session_id) || !text(receipt.run_id)) throw new RequestError(502);
    return { sessionId: receipt.session_id, runId: receipt.run_id };
  }

  async run(runId, signal) {
    const response = await this.#request(this.#scope("/runs/" + encodeURIComponent(runId)), signal);
    return text((await response.json()).state);
  }

  async #ticket(session, signal) {
    if (!session || !this.#workflow || session !== this.#mainId) return null;
    const path = "/omniweave/sessions/" + encodeURIComponent(session) + "/tickets";
    try {
      const response = await this.#request(this.#scope(path), signal);
      return await response.json();
    } catch (error) {
      if (error instanceof RequestError && error.status === 404) return null;
      throw error;
    }
  }

  async #workers(ticket, run, signal) {
    if (Array.isArray(ticket?.tasks) && ticket.tasks.length) {
      return ticket.tasks.filter(record).map((task) => ({
        label: text(task.profile) || "协作角色",
        state: text(task.state),
        detail: text(task.key),
        kind: "ticket",
        id: text(task.key),
        parentId: null,
        profile: nullableText(task.profile),
        profileVersion: nullableInteger(task.profile_version),
        goalVersion: nullableInteger(task.goal_version),
        reusedFromVersion: nullableInteger(task.reused_from_version),
        summary: null,
        summaryTruncated: null,
        summaryRedacted: null,
        errorCode: nullableText(task.error_code),
        unknownEffect: nullableBoolean(task.unknown_effect),
        unknownModelCalls: nullableInteger(task.unknown_model_calls),
      }));
    }
    const parent = await run();
    if (!parent) return [];
    const path = "/runs/" + encodeURIComponent(parent.id) + "/delegations";
    const response = await this.#request(this.#scope(path), signal);
    return items(await response.json()).map((row, index) => ({
      label: "协作 Worker " + (index + 1),
      state: text(row.state),
      detail: row.summary_redacted === true ? "" : text(row.summary),
      kind: "delegation",
      id: text(row.child_run_id),
      parentId: text(parent.id),
      profile: null,
      profileVersion: null,
      goalVersion: null,
      reusedFromVersion: null,
      summary: row.summary_redacted === true ? null : nullableText(row.summary),
      summaryTruncated: nullableBoolean(row.summary_truncated),
      summaryRedacted: nullableBoolean(row.summary_redacted),
      errorCode: nullableText(row.error_code),
      unknownEffect: nullableBoolean(row.unknown_effect),
      unknownModelCalls: nullableInteger(row.unknown_model_calls),
    }));
  }

  async workers(session, signal) {
    const ticket = await this.#ticket(session, signal);
    return this.#workers(ticket, async () => (await this.runs(session, signal)).at(-1), signal);
  }

  async #detail(read, signal) {
    try {
      return await read();
    } catch (error) {
      if (signal?.aborted) throw error;
      return null;
    }
  }

  async workerDetail(worker, signal) {
    const empty = () => ({ messages: null, events: null, tools: null });
    if (!record(worker) || worker.kind !== "delegation" || worker.summaryRedacted === true) return empty();
    const childId = text(worker.id);
    const parentId = text(worker.parentId);
    if (!childId || !parentId) return empty();
    let child;
    try {
      child = await (await this.#request(this.#scope("/runs/" + encodeURIComponent(childId)), signal)).json();
    } catch (error) {
      if (signal?.aborted) throw error;
      return empty();
    }
    if (!record(child) || text(child.id) !== childId || text(child.parent_id) !== parentId) return empty();
    const session = text(child.session_id);
    const [messages, events, tools] = await Promise.all([
      session
        ? this.#detail(async () => (await this.messages(session, signal)).map(({ role, text: body, at }) => ({ role, text: body, at })), signal)
        : Promise.resolve(null),
      this.#detail(async () => items(await (await this.#request(
        this.#scope("/runs/" + encodeURIComponent(childId) + "/events?limit=100"), signal,
      )).json()).map((event) => ({ kind: text(event.kind), at: text(event.created_at) })), signal),
      this.#detail(async () => {
        const report = await (await this.#request(
          this.#scope("/runs/" + encodeURIComponent(childId) + "/routing"), signal,
        )).json();
        const names = new Set();
        for (const call of Array.isArray(report?.calls) ? report.calls.filter(record) : []) {
          for (const tool of Array.isArray(call.tools) ? call.tools.filter(record) : []) {
            const name = text(tool.name);
            if (name) names.add(name);
          }
        }
        return [...names];
      }, signal),
    ]);
    return { messages, events, tools };
  }

  async #tasks(session, ticket, signal) {
    const response = await this.#request(this.#scope("/sessions/" + encodeURIComponent(session) + "/todos"), signal);
    const snapshot = await response.json();
    const todos = Array.isArray(snapshot.todos) ? snapshot.todos.filter(record) : [];
    const ticketTasks = Array.isArray(ticket?.tasks) ? ticket.tasks.filter(record) : [];
    return [
      ...ticketTasks.map((task) => ({ label: text(task.key) || "协作任务", state: text(task.state) })),
      ...todos.map((todo) => ({ label: text(todo.content) || "待办事项", state: text(todo.status) })),
    ];
  }

  async tasks(session, signal) {
    if (!session) return [];
    return this.#tasks(session, await this.#ticket(session, signal), signal);
  }

  async coordination(session, latest, signal) {
    if (!session) return { ticket: null, workers: [], tasks: [] };
    const ticket = await this.#ticket(session, signal);
    const [workers, tasks] = await Promise.allSettled([
      this.#workers(ticket, () => latest, signal),
      this.#tasks(session, ticket, signal),
    ]);
    return {
      ticket: ticket ? { id: text(ticket.ticket_id), version: ticket.goal_version } : null,
      workers: workers.status === "fulfilled" ? workers.value : null,
      tasks: tasks.status === "fulfilled" ? tasks.value : null,
    };
  }

  async approvals(signal) {
    const response = await this.#request(this.#scope("/approvals"), signal);
    return items(await response.json()).filter((row) => text(row.id) && Number.isInteger(row.policy_revision))
      .map((row) => ({
        id: row.id,
        runId: text(row.run_id),
        tool: text(row.tool),
        arguments: record(row.arguments) ? row.arguments : null,
        version: row.policy_revision,
      }));
  }

  async decide(request, allowed, signal) {
    const path = "/approvals/" + encodeURIComponent(request.id);
    const response = await this.#request(this.#scope(path), signal, {
      allowed, policy_revision: request.version,
    });
    if (!(await response.json()).resolved) throw new RequestError(502);
  }

  async capabilities(signal) {
    const response = await this.#request("/capabilities", signal);
    const value = await response.json();
    if (!Array.isArray(value.areas)) return [];
    return value.areas.filter(record).filter((area) => text(area.name))
      .map((area) => ({ label: area.name, state: text(area.state) }));
  }}
