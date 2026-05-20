from __future__ import annotations

import difflib
import json
import re

from configs.config import (
    ENABLE_JUDGE,
    ALLOWED_SKILLS,
    ENABLE_LOOP,
    ENABLE_MEMORY,
    ENABLE_PLANNING,
    ENABLE_SKILLS,
    ENABLE_SUMMARIZATION,
    ENABLE_TOOLS,
)
from core.catalog import load_skill_documents, load_tool_documents
from core.files import read_text
from core.llm import call_llm, MODEL_4B
from core.memory import append_history, load_memory, save_memory
from core.telemetry import log_skill_usage
from core.actions import dispatch_action
from core.workspace import get_active_experiment_id
from configs.config import TARGET_HOST as CONFIG_TARGET_HOST
from configs.config import TARGET_PORT as CONFIG_TARGET_PORT


AGENTS_PATH = "AGENTS.md"
GLOBAL_PLANNER_PROMPT_PATH = "prompts/global_planner.md"
INSTRUCTOR_PROMPT_PATH = "prompts/instructor.md"
SUMMARIZER_PROMPT_PATH = "prompts/summarizer.md"
JUDGE_PROMPT_PATH = "prompts/judge.md"

TARGET = CONFIG_TARGET_HOST
TARGET_PORT = CONFIG_TARGET_PORT


def print_block(title: str, content: str) -> None:
    print(f"\n--- {title} ---")
    print(content)


def build_base_prompt() -> str:
    agents = read_text(AGENTS_PATH)
    capability_summary = f"""
[capabilities]
planning={ENABLE_PLANNING}
memory={ENABLE_MEMORY}
summarization={ENABLE_SUMMARIZATION}
judge={ENABLE_JUDGE}
loop={ENABLE_LOOP}
tools={ENABLE_TOOLS}
skills={ENABLE_SKILLS}
""".strip()
    tool_documents = load_tool_documents()
    skill_documents = load_skill_documents()

    parts = [agents, capability_summary]
    if tool_documents:
        parts.append("[available tools]\n" + tool_documents)
    if skill_documents:
        parts.append("[available skills]\n" + skill_documents)
    return "\n\n".join(parts)


def build_role_prompt(path: str) -> str:
    base = build_base_prompt()
    role = read_text(path)
    return f"{base}\n\n{role}"


def global_planner(system_prompt: str, global_plan: str, memory: str, last_result: str) -> list[str]:
    user_prompt = f"""
[current target]
host: {TARGET}
port: {TARGET_PORT}

[current global plan]
{global_plan}

[current memory]
{memory}

[last result]
{last_result}

현재 글로벌 플랜을 실행 가능한 세부 플랜들로 나누어라.
반드시 `1. ...` 형식의 평문 목록만 출력하라.
"""
    print_block("GLOBAL PLANNER INPUT", user_prompt.strip())

    try:
        output = call_llm(
            model=MODEL_4B,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=600,
            trace_role="global_planner",
            trace_skills=ALLOWED_SKILLS if ENABLE_SKILLS else [],
        ).strip()
    except Exception as exc:
        fallback = [global_plan]
        print_block("GLOBAL PLANNER ERROR", str(exc))
        print_block("GLOBAL PLANNER FALLBACK", "\n".join(fallback))
        return fallback

    print_block("GLOBAL PLANNER OUTPUT", output)
    return parse_numbered_list(output)


def instructor(system_prompt: str, global_plan: str, detailed_plan: str, memory: str, last_result: str) -> str:
    user_prompt = f"""
[current target]
host: {TARGET}
port: {TARGET_PORT}

[current global plan]
{global_plan}

[current detailed plan]
{detailed_plan}

[current memory]
{memory}

[last result]
{last_result}

현재 세부 플랜에 맞는 다음 한 단계 행동만 결정하라.
placeholder를 쓰지 말고 실제 host, port를 사용하라.
도구 카탈로그에 callable_by_agent: yes 로 표시된 기능은 가능하면 tool 행동으로 우선 호출하라.

반드시 JSON만 출력하라.
형식:
{{
  "action": "tool" | "shell" | "submit_flag" | "final_answer" | "noop",
  "args": {{}}
}}
"""
    print_block("INSTRUCTION INPUT", user_prompt.strip())

    try:
        output = call_llm(
            model=MODEL_4B,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=400,
            trace_role="instructor",
            trace_skills=ALLOWED_SKILLS if ENABLE_SKILLS else [],
        ).strip()
    except Exception as exc:
        output = json.dumps(
            {
                "action": "noop",
                "args": {"reason": f"instructor llm error: {exc}"},
            },
            ensure_ascii=False,
        )

    print_block("INSTRUCTION OUTPUT", output)
    return output


def summarize_memory(system_prompt: str, old_memory: str, execution_result: str) -> str:
    user_prompt = f"""
[old memory]
{old_memory}

[new execution result]
{execution_result}

다음 planner가 바로 다음 행동을 정할 수 있도록 결과 중심으로 요약하라.
무엇이 발견됐는지, 왜 유의미한지 반드시 포함하라.
명령어 자체를 반복하지 말고 관찰된 사실만 남겨라.
plain text만 출력하라.
"""
    print_block("SUMMARIZER INPUT", user_prompt.strip())

    try:
        output = call_llm(
            model=MODEL_4B,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.2,
            max_tokens=300,
            trace_role="summarizer",
            trace_skills=ALLOWED_SKILLS if ENABLE_SKILLS else [],
        ).strip()
    except Exception as exc:
        print_block("SUMMARIZER ERROR", str(exc))
        return build_heuristic_summary(execution_result)

    print_block("SUMMARIZER OUTPUT", output)
    return normalize_memory_summary(output, execution_result)


def parse_response_json(execution_result: str) -> dict:
    response = extract_result_section(execution_result, "response")
    if not response:
        return {}
    try:
        payload = json.loads(response)
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def parse_request_body_fields(execution_result: str) -> dict[str, str]:
    response_payload = parse_response_json(execution_result)
    request_body = response_payload.get("request_body")
    if not isinstance(request_body, str) or not request_body.strip():
        return {}

    raw = request_body.strip()
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):
            return {str(key): str(value) for key, value in loaded.items()}
    except Exception:
        pass

    if "=" in raw:
        parsed: dict[str, str] = {}
        for part in raw.split("&"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            parsed[key] = value
        return parsed

    return {}


def build_structured_memory_notes(execution_result: str) -> str:
    lines: list[str] = []
    lowered = execution_result.lower()
    response_payload = parse_response_json(execution_result)
    request_fields = parse_request_body_fields(execution_result)
    response_body = str(response_payload.get("body", ""))
    response_lowered = response_body.lower()
    url = str(response_payload.get("url", "")).strip()
    status_code = str(response_payload.get("status_code", "")).strip()

    if "[status]\nskipped_duplicate" in lowered:
        flag = extract_result_section(execution_result, "flag").strip()
        if flag:
            lines.append(f"{flag} 는 현재 실험에서 이미 제출된 플래그이므로 다시 제출하지 않는다.")

    if "[action_error]\nunknown_action" in lowered:
        attempted_action = extract_result_section(execution_result, "action").strip()
        if attempted_action:
            lines.append(
                f"{attempted_action} 형태의 직접 action 은 지원되지 않았고 실패했다. 같은 형식은 반복하지 않는다."
            )

    if "invalid_action_json" in lowered:
        lines.append("직전 행동 JSON 형식이 잘못되어 실행에 실패했다. 다음 행동은 엄격한 JSON 형식만 사용한다.")

    invalid_login_signals = [
        "invalid username or password",
        "login failed",
        "로그인 실패",
    ]
    if any(signal in response_lowered or signal in lowered for signal in invalid_login_signals):
        username = request_fields.get("username", "").strip()
        password = request_fields.get("password", "").strip()
        if username or password:
            lines.append(
                f"로그인 조합 username={username or '-'} password={password or '-'} 는 실패했으므로 같은 조합 재시도 우선순위를 낮춘다."
            )
        else:
            lines.append("직전 로그인 시도는 실패했으므로 동일한 자격 증명 재사용을 피한다.")

    if (
        url.endswith("/login")
        and status_code == "200"
        and request_fields.get("username")
        and request_fields.get("password")
        and "invalid username or password" in response_lowered
    ):
        username = request_fields.get("username", "").strip()
        password = request_fields.get("password", "").strip()
        lines.append(
            f"/login POST 결과 로그인 페이지와 invalid username or password가 돌아왔으므로 username={username or '-'} password={password or '-'} 조합은 유효하지 않다."
        )

    if response_contains_empty_session_delete(execution_result):
        if url:
            lines.append(
                f"{url} 응답에서 session 삭제 쿠키가 설정되었으므로 사용한 세션은 인증에 실패했거나 무효하다."
            )
        else:
            lines.append("응답이 session 삭제 쿠키를 설정했으므로 사용한 세션은 인증에 실패했거나 무효하다.")

    return "\n".join(dict.fromkeys(line for line in lines if line.strip()))


def judge_result(
    system_prompt: str,
    global_plan: str,
    detailed_plan: str,
    memory_summary: str,
    execution_result: str,
) -> tuple[bool, str]:
    user_prompt = f"""
[current global plan]
{global_plan}

[current detailed plan]
{detailed_plan}

[memory summary]
{memory_summary}

[execution result]
{execution_result}

현재 세부 플랜이 이번 반복에서 성공했는지 판단하라.
반드시 JSON만 출력하라.
형식:
{{
  "successful": true | false,
  "reason": "..."
}}
"""
    print_block("JUDGE INPUT", user_prompt.strip())

    try:
        output = call_llm(
            model=MODEL_4B,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            max_tokens=300,
            trace_role="judge",
            trace_skills=ALLOWED_SKILLS if ENABLE_SKILLS else [],
        ).strip()
    except Exception as exc:
        print_block("JUDGE ERROR", str(exc))
        return classify_result(execution_result)

    print_block("JUDGE OUTPUT", output)
    return normalize_judgment(output, execution_result)


def normalize_memory_summary(summary: str, execution_result: str) -> str:
    cleaned = summary.strip()

    if is_poor_summary(cleaned):
        heuristic = build_heuristic_summary(execution_result)
        if heuristic:
            return heuristic

    structured = build_structured_memory_notes(execution_result)
    if cleaned and structured:
        return "\n".join(dict.fromkeys([*cleaned.splitlines(), *structured.splitlines()]))
    if structured:
        return structured

    return cleaned


def merge_memory(old_memory: str, new_summary: str) -> str:
    old_clean = old_memory.strip()
    new_clean = new_summary.strip()

    if not old_clean:
        return new_clean

    if not new_clean or new_clean in old_clean:
        return old_clean

    parts = [line.strip() for line in old_clean.splitlines() if line.strip()]
    for line in new_clean.splitlines():
        cleaned = line.strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)

    return "\n".join(parts[-8:])


def summarize_fact(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = normalized.replace("`", "")
    replacements = {
        "werkzeug/3.1.8 python/3.11.0": "werkzeug",
        "200 ok": "http 200",
        "set-cookie:": "cookie:",
        "session=": "session-cookie",
        "/login": "login-endpoint",
        "/flag": "flag-endpoint",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized


def extract_fact_markers(text: str) -> set[str]:
    lowered = text.lower()
    markers: set[str] = set()

    if "werkzeug" in lowered:
        markers.add("server:werkzeug")
    if "/login" in lowered or "login-endpoint" in lowered:
        markers.add("endpoint:login")
    if "/flag" in lowered or "flag-endpoint" in lowered:
        markers.add("endpoint:flag")
    if "200 ok" in lowered or "http 200" in lowered or "status_code\": 200" in lowered:
        markers.add("status:200")
    if "session=" in lowered or "session-cookie" in lowered or "set-cookie" in lowered:
        markers.add("artifact:session-cookie")
    if "302" in lowered or "redirect" in lowered:
        markers.add("artifact:redirect")
    if "로그인" in lowered or "authentication" in lowered or "인증" in lowered:
        markers.add("state:auth-related")
    if "폼" in lowered or "<form" in lowered or "input" in lowered:
        markers.add("artifact:login-form")
    if "invalid username or password" in lowered or "로그인 조합" in lowered:
        markers.add("state:login-failure")
    if "이미 제출된 플래그" in lowered or "skipped_duplicate" in lowered or "다시 제출하지 않는다" in lowered:
        markers.add("state:duplicate-flag")
    if "직접 action" in lowered and "실패" in lowered:
        markers.add("state:invalid-action-shape")
    if "session 삭제 쿠키" in lowered or "session=;" in lowered:
        markers.add("state:invalid-session")

    credential_match = re.search(r"username=([^\s]+)\s+password=([^\s]+)", lowered)
    if credential_match:
        markers.add(f"credential:{credential_match.group(1)}:{credential_match.group(2)}")

    return markers


def memory_contains_fact(memory: str, summary: str) -> bool:
    candidate = summarize_fact(summary)
    if not candidate:
        return True

    existing_lines = [line.strip() for line in memory.splitlines() if line.strip()]
    existing_facts = {summarize_fact(line) for line in existing_lines}
    if candidate in existing_facts:
        return True

    candidate_markers = extract_fact_markers(summary)
    for line in existing_lines:
        normalized = summarize_fact(line)
        if difflib.SequenceMatcher(None, normalized, candidate).ratio() >= 0.82:
            return True

        line_markers = extract_fact_markers(line)
        if candidate_markers and candidate_markers.issubset(line_markers):
            return True

    return False


def parse_numbered_list(text: str) -> list[str]:
    items: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\d+\.\s*(.+)$", line)
        if match:
            items.append(match.group(1).strip())

    if items:
        return items

    fallback = [line.strip() for line in text.splitlines() if line.strip()]
    return fallback


def normalize_judgment(output: str, execution_result: str) -> tuple[bool, str]:
    try:
        data = json.loads(output)
        successful = bool(data["successful"])
        reason = str(data.get("reason", "")).strip() or default_judgment_reason(successful, execution_result)
        return successful, reason
    except Exception:
        return classify_result(execution_result)


def is_poor_summary(summary: str) -> bool:
    lowered = summary.lower()

    if not summary:
        return True

    bad_patterns = [
        lowered.startswith("nmap "),
        lowered.startswith("curl "),
        lowered.startswith("python "),
        "[command]" in lowered,
        '"action"' in lowered,
        '"args"' in lowered,
    ]

    return any(bad_patterns)


def build_heuristic_summary(execution_result: str) -> str:
    lines: list[str] = []

    port_match = re.search(r"(\d+)/tcp open\s+(\S+)\s+(.+)", execution_result)
    if port_match:
        port, service, detail = port_match.groups()
        lines.append(f"{port}/tcp 에서 {service} 서비스가 열려 있고 {detail.strip()} 가 식별됨.")

    header_match = re.search(r"_http-server-header:\s+(.+)", execution_result)
    if header_match:
        header = header_match.group(1).strip()
        lines.append(f"서버 헤더는 {header} 로 보이며 웹 애플리케이션 프레임워크 정보 확인에 유의미함.")

    title_match = re.search(r"_http-title:\s+(.+)", execution_result)
    if title_match:
        title = title_match.group(1).strip()
        lines.append(f"HTTP 타이틀이 {title} 로 응답해 웹 페이지가 실제로 노출되어 있음.")

    if not lines and "host is up" in execution_result.lower():
        lines.append("대상 호스트가 응답하며 추가 HTTP 정찰이 가능한 상태임.")

    return "\n".join(lines[:3]).strip()


def result_contains_real_login_form(execution_result: str) -> bool:
    response = extract_result_section(execution_result, "response")
    lowered = response.lower()
    return "<form" in lowered and "username" in lowered and "password" in lowered


def result_is_post_request(execution_result: str) -> bool:
    response = extract_result_section(execution_result, "response")
    return '"method": "POST"' in response or '"method":"POST"' in response


def result_targets_flag_endpoint(execution_result: str) -> bool:
    response = extract_result_section(execution_result, "response")
    return '"url": "http://192.168.0.17:7587/flag"' in response or '"url":"http://192.168.0.17:7587/flag"' in response


def enforce_plan_completion(detailed_plan: str, execution_result: str, successful: bool, reason: str) -> tuple[bool, str]:
    lowered_plan = detailed_plan.lower()
    if not successful:
        return successful, reason

    if "start_experiment" in lowered_plan:
        if "409 client error: conflict" in execution_result.lower():
            return True, "이미 활성 실험이 있어 start 단계는 사실상 충족된 것으로 간주함"
        if "[tool_name]\nstart_experiment" not in execution_result:
            return False, "세부 플랜이 요구한 start_experiment 실행이 실제로 수행되지 않음"

    if "/login" in lowered_plan and ("input" in lowered_plan or "form" in lowered_plan):
        if not result_contains_real_login_form(execution_result):
            return False, "로그인 폼 필드 추출이 요구됐지만 실제 폼 구조를 확인한 근거가 부족함"

    if "post" in lowered_plan:
        if not result_is_post_request(execution_result):
            return False, "세부 플랜은 POST 단계였지만 실제 실행은 POST 요청으로 이어지지 않음"

    if "/flag" in lowered_plan:
        if not result_targets_flag_endpoint(execution_result):
            return False, "세부 플랜은 /flag 확인 단계였지만 실제 응답은 /flag 대상으로 확정되지 않음"

    return True, reason


def default_judgment_reason(successful: bool, execution_result: str) -> str:
    if successful:
        return "실행 결과에 다음 단계로 이어질 식별 정보가 확인됨"
    return classify_result(execution_result)[1]


def extract_result_section(result: str, section_name: str) -> str:
    match = re.search(
        rf"\[{re.escape(section_name)}\]\n(.*?)(?=\n\[[^\n]+\n|\Z)",
        result,
        re.DOTALL,
    )
    if not match:
        return ""
    return match.group(1).strip()


def response_contains_empty_session_delete(execution_result: str) -> bool:
    response = extract_result_section(execution_result, "response")
    lowered = response.lower()
    return (
        "set-cookie" in lowered
        and "session=;" in lowered
        and ("expires=thu, 01 jan 1970" in lowered or "max-age=0" in lowered)
    )





def classify_result(result: str) -> tuple[bool, str]:
    lowered = result.lower()
    command = extract_result_section(result, "command")
    stdout = extract_result_section(result, "stdout")
    stderr = extract_result_section(result, "stderr")
    response = extract_result_section(result, "response")
    action = extract_result_section(result, "action")
    returncode_raw = extract_result_section(result, "returncode")

    try:
        returncode = int(returncode_raw) if returncode_raw else None
    except ValueError:
        returncode = None

    bad_signals = [
        "failed to resolve",
        "no targets were specified",
        "0 hosts up",
        "invalid_action_json",
        "unknown_action",
        "[returncode]\n127",
        "[returncode]\n1",
        "not allowed by configuration",
        "tool execution is disabled",
    ]

    if any(signal in lowered for signal in bad_signals):
        return False, "오류 신호가 포함되어 후속 판단에 쓸 만한 발견이 없음"

    if response_contains_empty_session_delete(result):
        return False, "응답이 session 삭제 쿠키(session=; Max-Age=0)를 설정했으므로 인증 성공으로 볼 수 없음"

    if action == "submit_flag":
        if '"result": "correct"' in lowered or '"is_correct": true' in lowered:
            return True, "플래그 제출이 정답으로 수락되어 점수화 경로까지 완료됨"
        return False, "플래그 제출 응답에서 정답 처리 근거를 확인하지 못함"

    if returncode not in (None, 0):
        return False, "명령이 비정상 종료되어 이번 세부 플랜의 진전으로 보기 어려움"

    if command.startswith("nmap") and re.search(r"\d+/tcp open\s+\S+", result):
        return True, "nmap으로 열린 서비스가 실제 식별되어 다음 단계 정찰에 활용 가능함"

    if command.startswith("curl"):
        meaningful_http = [
            "연구용 보호 서버",
            "<html",
            "200 ok",
            "/login",
            "로그인",
            '"status": "ok"',
            '"status":"ok"',
        ]
        if any(signal in lowered for signal in meaningful_http):
            return True, "HTTP 응답 본문이나 상태에서 실제 웹 엔드포인트가 확인됨"

    if command.startswith("tshark"):
        sniff_signals = [
            "net.sniff.http.request",
            "net.sniff.http.response",
            "http ",
            "packets",
            "length",
        ]
        if any(signal in lowered for signal in sniff_signals):
            return True, "스니핑 결과가 실제로 수집되어 MITM 또는 트래픽 관찰이 진행됨"

    if "flag{" in stdout.lower() or "flag{" in response.lower():
        return True, "플래그 형식 문자열이 관찰되어 제출 단계로 진행 가능함"

    return False, "후속 행동을 바꿀 만큼의 식별 정보가 부족함"

def build_submit_flag_instruction_from_memory(memory: str) -> str | None:
    match = re.search(r"flag\{[^}\n]+\}", memory, re.IGNORECASE)
    if not match:
        return None

    return json.dumps(
        {
            "action": "submit_flag",
            "args": {
                "flag": match.group(0)
            },
        },
        ensure_ascii=False,
    )


def build_detailed_plans(global_plan: str, last_result: str) -> list[str]:
    if not ENABLE_PLANNING:
        append_history("CURRENT GLOBAL PLAN", global_plan)
        append_history("DETAILED PLANS", f"1. {global_plan}")
        return [global_plan]

    global_planner_system_prompt = build_role_prompt(GLOBAL_PLANNER_PROMPT_PATH)
    memory = load_memory()
    detailed_plans = global_planner(global_planner_system_prompt, global_plan, memory, last_result)
    try:
        get_active_experiment_id()
        filtered = [
            plan
            for plan in detailed_plans
            if "start_experiment" not in plan.lower()
        ]
        if filtered:
            detailed_plans = filtered
    except RuntimeError:
        pass

    append_history("CURRENT GLOBAL PLAN", global_plan)
    append_history("DETAILED PLANS", "\n".join(f"{idx}. {plan}" for idx, plan in enumerate(detailed_plans, start=1)))
    return detailed_plans


def run_cycle(last_result: str, global_plan: str, detailed_plan: str) -> tuple[str, bool, str]:
    instructor_system_prompt = build_role_prompt(INSTRUCTOR_PROMPT_PATH)
    summarizer_system_prompt = build_role_prompt(SUMMARIZER_PROMPT_PATH)
    judge_system_prompt = build_role_prompt(JUDGE_PROMPT_PATH)
    memory = load_memory()

    print_block("LOADED MEMORY", memory if memory else "(empty)")
    print_block("CURRENT GLOBAL PLAN", global_plan)
    print_block("CURRENT DETAILED PLAN", detailed_plan)

    shortcut_instruction = build_submit_flag_instruction_from_memory(memory)

    if shortcut_instruction:
        instruction = shortcut_instruction
        log_skill_usage(skill="flag_submission", phase="memory_shortcut", detail="flag found in memory")
        print_block("INSTRUCTION SHORTCUT", "flag candidate found in memory; submit_flag selected without LLM.")
    else:
        instruction = instructor(instructor_system_prompt, global_plan, detailed_plan, memory, last_result)

    append_history("CURRENT GLOBAL PLAN", global_plan)
    append_history("CURRENT DETAILED PLAN", detailed_plan)
    append_history("INSTRUCTION", instruction)

    print_block("DISPATCH ACTION", instruction)
    execution_result = dispatch_action(instruction)
    print_block("EXECUTION RESULT", execution_result)
    append_history("EXECUTION RESULT", execution_result)

    if ENABLE_MEMORY:
        if ENABLE_SUMMARIZATION:
            new_summary = summarize_memory(summarizer_system_prompt, memory, execution_result)
        else:
            new_summary = build_heuristic_summary(execution_result)

        if memory_contains_fact(memory, new_summary):
            merged_memory = memory
            new_summary = ""
        else:
            merged_memory = merge_memory(memory, new_summary)
        save_memory(merged_memory)
        append_history("MEMORY SUMMARY", new_summary or "(duplicate fact omitted)")
        print_block("UPDATED MEMORY", merged_memory)
    else:
        new_summary = ""
        merged_memory = ""

    judge_memory = merged_memory or new_summary or memory

    if ENABLE_JUDGE:
        successful, reason = judge_result(
            judge_system_prompt,
            global_plan,
            detailed_plan,
            judge_memory,
            execution_result,
        )
    else:
        successful, reason = classify_result(execution_result)
        reason = f"[judge disabled] {reason}"
    successful, reason = enforce_plan_completion(detailed_plan, execution_result, successful, reason)
    append_history("JUDGMENT", reason)

    if successful:
        append_history("DETAILED PLAN COMPLETE", detailed_plan)
        print_block("JUDGMENT", f"세부 플랜 완료: {reason}")
        return execution_result, True, reason

    append_history("DETAILED PLAN RETRY", detailed_plan)
    print_block("JUDGMENT", f"세부 플랜 재시도: {reason}")
    return execution_result, False, reason
