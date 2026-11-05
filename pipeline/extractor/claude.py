"""`claude -p` 한 번 부르기 (#14). 원문은 표준 입력으로만 넘기고, 도구를 모두 끈 채 빈 임시 폴더에서 실행한다.

공지 본문의 "파일을 읽어라" 같은 주입은 프롬프트 문구만으로 막을 수 없어서, 실행 조건으로 막는다.
- `--tools ""`: 내장 도구를 모두 끈다 (이미지는 #12 뒤에 Read만 입력 폴더로 열 예정).
- `--disallowedTools "mcp__*"`: `--tools ""`는 MCP 도구를 끄지 않아 따로 막는다. 환경변수로 claude.ai 커넥터도 끈다.
- `--safe-mode`: CLAUDE.md·스킬·플러그인·훅·MCP 서버·자동 메모리를 읽지 않는다 (인증은 그대로라 구독 로그인으로 돈다.
  `--bare`는 API 키만 받아 쓰지 않는다). `--strict-mcp-config`: 설정 파일의 MCP 서버를 쓰지 않는다.
  `--settings {"disableAllHooks": true}`: 훅을 한 번 더 끈다.
- `--no-session-persistence`: 대화 기록을 남기지 않는다. 빈 임시 폴더: 주변 파일을 읽지 않는다.
- `--system-prompt-file`: 기본 시스템 프롬프트를 v0.2 지시로 바꾼다 (에이전트용 지시·도구 설명이 섞이지 않게).
- `--json-schema`: 출력 형식을 강제한다. 받은 뒤 코드가 한 번 더 모양을 본다.
- `--max-turns`는 쓰지 않는다: 이 버전 도움말에 없고, 도구가 없으면 한 번 답하고 끝난다.
- **격리는 실측한다:** `--output-format stream-json`의 시작 이벤트(init)에서 쓸 수 있는 도구·MCP 서버가 비어 있는지,
  로그인 계정으로 도는지(apiKeySource) 본다. 실행을 시작할 때 공지 없이 한 번 불러(probe) 확인하고, 호출마다 다시 확인해
  하나라도 어긋나면 결과를 버리고 멈춘다 (플래그의 뜻이 CLI 버전마다 달라도 열린 채로 공지를 보내지 않게).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from extractor.prompt import PROMPT_DIR

REPO = Path(__file__).resolve().parents[2]
MAX_COMMAND_CHARS = 30000  # Windows CreateProcess 한도 32,767 UTF-16 단위보다 여유 있게 (문자 수로 센다)
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_\[\]]{0,80}")
# 이 값들이 있으면 구독 로그인 대신 그 계정·프록시로 간다 (회사·다른 사람 계정일 수 있다)
ACCOUNT_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK",
                  "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN")
STDERR_CHARS = 200
# --json-schema가 안에서 쓰는 결과 제출 도구는 파일·명령·네트워크에 닿지 않아 열려 있어도 된다 (이름이 버전마다 다를 수 있다)
INTERNAL_TOOL = re.compile(r"(?i)structured_?output")
CLOSED_SERVER_STATUS = {"disabled", "failed"}


class ClaudeError(Exception):
    """쓸 수 있는 결과가 없음. 메시지는 고정 문구 + 종류 (공지 내용·모델 출력을 담지 않는다).

    kind: "setup"(실행·설정 문제: 다음 대회도 같은 이유로 실패한다), "isolation"(격리가 확인되지 않음: 바로 멈춘다),
          "model"(모델이 형식에 맞게 답하지 못함: 모델 비교에서 재는 대상이라 다음 대회로 간다).
    usage·cost_usd: 실패한 호출도 토큰을 쓴다 (비용 환산에 넣는다).
    """

    def __init__(self, message: str, usage: dict | None = None, cost_usd: float | None = None, kind: str = "setup"):
        super().__init__(message)
        self.usage, self.cost_usd, self.kind = usage or {}, cost_usd, kind


@dataclass
class CallResult:
    output: dict
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    seconds: float = 0.0
    models: list[str] = field(default_factory=list)  # 실제로 응답한 모델 ID (별칭 haiku 등이 무엇이었는지)


def account_env_in_use() -> list[str]:
    return [name for name in ACCOUNT_ENV if os.environ.get(name)]


def resolve_claude(command: str) -> str:
    """실행할 claude 경로. npm의 claude.CMD는 cmd.exe를 거쳐 명령줄이 8,191자로 잘리므로 옆의 claude.exe를 직접 쓴다.
    저장소 안의 claude는 쓰지 않는다 (커밋된 가짜 실행 파일이 현재 폴더에서 먼저 잡히지 않게)."""
    found = shutil.which(command)
    if found is None:
        raise ClaudeError(f"claude 실행 파일을 찾지 못함: {command}")
    path = Path(found).resolve()
    if path.is_relative_to(REPO):
        raise ClaudeError(f"저장소 안의 claude는 쓰지 않음: {path}")
    if path.suffix.lower() in (".cmd", ".bat"):
        exe = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if not exe.exists():
            raise ClaudeError(f"{path.name}는 명령줄이 8,191자로 잘려 쓸 수 없고 claude.exe도 찾지 못함 (--claude로 claude.exe 경로를 주기)")
        return str(exe)
    return str(path)


def build_command(claude: str, model: str) -> list[str]:
    """Raises: ClaudeError (모델 이름 형식·명령줄 길이)."""
    if not MODEL_NAME.fullmatch(model):
        raise ClaudeError(f"모델 이름 형식이 다름: {model[:40]!r}")
    schema = json.dumps(json.loads((PROMPT_DIR / "schema.json").read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
    command = [claude, "-p", "--output-format", "stream-json", "--verbose", "--json-schema", schema, "--model", model,
               "--system-prompt-file", str(PROMPT_DIR / "system.md"), "--tools", "", "--disallowedTools", "mcp__*",
               "--safe-mode", "--strict-mcp-config", "--settings", '{"disableAllHooks": true}', "--no-session-persistence"]
    if sum(len(part) + 3 for part in command) > MAX_COMMAND_CHARS:
        raise ClaudeError("명령줄이 너무 김 (schema.json을 줄이기)")
    return command


def child_env(allow_env_account: bool) -> dict:
    env = dict(os.environ)
    env["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"  # 계정에 붙은 claude.ai 커넥터(Notion 등)를 쓰지 않는다
    if not allow_env_account:
        for name in ACCOUNT_ENV:
            env.pop(name, None)
    return env


def _run(text: str, claude: str, model: str, timeout: int, allow_env_account: bool):
    """claude를 한 번 실행 → (종료 코드, init 이벤트, result 이벤트, stderr, 걸린 초)."""
    command = build_command(claude, model)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mjr-extract-") as workdir:
        try:
            # 바이트로 넘긴다: 텍스트 모드는 Windows에서 줄바꿈을 \r\n으로 바꿔 근거 대조(#15)와 어긋난다
            completed = subprocess.run(command, input=text.encode("utf-8"), capture_output=True, cwd=workdir,
                                       timeout=timeout, env=child_env(allow_env_account))
        except subprocess.TimeoutExpired:
            raise ClaudeError(f"{timeout}초 안에 끝나지 않음", kind="model") from None
        except OSError as error:
            raise ClaudeError(f"실행하지 못함 ({type(error).__name__})") from None
    events = _events(completed.stdout.decode("utf-8", errors="replace"))
    init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), None)
    response = next((event for event in reversed(events) if event.get("type") == "result"), None)
    return completed.returncode, init, response, completed.stderr, time.monotonic() - started


def _usage(response: dict) -> tuple[dict, float | None]:
    usage = {key: response["usage"][key] for key in USAGE_KEYS
             if isinstance(response.get("usage"), dict) and isinstance(response["usage"].get(key), int)}
    cost = response.get("total_cost_usd") if isinstance(response.get("total_cost_usd"), (int, float)) else None
    return usage, cost


def _isolation_problem(init, allow_env_account: bool) -> str | None:
    """init 이벤트로 격리를 확인한다. 모양이 다르면 확인하지 못한 것으로 본다 (열린 채로 통과하지 않게)."""
    if init is None:
        return "시작 이벤트(init)가 없어 격리를 확인하지 못함"
    tools, servers = init.get("tools"), init.get("mcp_servers")
    if not isinstance(tools, list) or not isinstance(servers, list):
        return "격리 확인 실패: init에 도구·MCP 서버 목록이 없음 (CLI 형식이 다름)"
    open_tools = [str(name) for name in tools if not (isinstance(name, str) and INTERNAL_TOOL.fullmatch(name))]
    open_servers = [server for server in servers if not (isinstance(server, dict) and server.get("status") in CLOSED_SERVER_STATUS)]
    if open_tools or open_servers:
        # 이름은 앞부분만 (mcp__서버__도구 → mcp__서버): 팀에 알릴 때 PC의 설정을 덜 드러내게
        names = sorted({"__".join(name.split("__")[:2]) for name in open_tools})
        return f"격리 확인 실패: 도구 {len(open_tools)}개 {','.join(names)[:60]}, MCP 서버 {len(open_servers)}개"
    source = init.get("apiKeySource")
    if not allow_env_account and isinstance(source, str) and source != "none":
        return f"계정 확인 실패: 로그인이 아닌 키로 돈다 (apiKeySource={source[:20]})"
    return None


def probe(claude: str, model: str, timeout: int = 300, allow_env_account: bool = False) -> tuple[dict, float | None]:
    """공지 없이 한 번 불러 격리·계정만 확인한다 (공지를 보내기 전에. 주입된 공지가 열린 환경으로 가지 않게).

    Returns: (토큰, 추정 비용). Raises: ClaudeError (setup·isolation). 모델 답의 형식은 보지 않는다.
    """
    _, init, response, stderr, _ = _run("이 입력에는 공지가 없다. 대회가 아니라고 답하라.", claude, model, timeout, allow_env_account)
    if response is None:
        raise ClaudeError(f"결과가 없음{_stderr_hint(stderr)}")
    usage, cost = _usage(response)
    problem = _isolation_problem(init, allow_env_account)
    if problem:
        raise ClaudeError(problem, usage, cost, kind="isolation")
    return usage, cost


def call(text: str, claude: str, model: str, timeout: int = 300, allow_env_account: bool = False) -> CallResult:
    """AI 입력 글 하나 → 추출 결과.

    Raises:
        ClaudeError: 실행 실패·시간 초과·오류 응답·격리 확인 실패·형식이 다른 출력 (kind로 구분)
    """
    returncode, init, response, stderr, seconds = _run(text, claude, model, timeout, allow_env_account)
    if response is None:
        raise ClaudeError(f"결과가 없음 (종료 코드 {returncode}{_stderr_hint(stderr)})")
    usage, cost = _usage(response)
    problem = _isolation_problem(init, allow_env_account)
    if problem:
        raise ClaudeError(problem, usage, cost, kind="isolation")
    if returncode != 0 or response.get("is_error") is True:
        subtype = response.get("subtype") if isinstance(response.get("subtype"), str) else "알 수 없음"
        raise ClaudeError(f"오류 응답 (종료 코드 {returncode}, {subtype[:40]})", usage, cost, kind="model")
    output = response.get("structured_output")
    if not isinstance(output, dict) and isinstance(response.get("result"), str):
        try:
            output = json.loads(response["result"])
        except ValueError:
            output = None
    if not _has_shape(output):
        raise ClaudeError("스키마 모양의 결과가 없음", usage, cost, kind="model")
    models = [name[:80] for name in response["modelUsage"]] if isinstance(response.get("modelUsage"), dict) else []
    return CallResult(output, usage, cost, seconds, models)


def _events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _stderr_hint(stderr: bytes) -> str:
    """결과가 없을 때만 CLI 오류의 첫 줄을 붙인다 (로그인 안 됨·모르는 옵션 등)."""
    first = next((line.strip() for line in stderr.decode("utf-8", errors="replace").splitlines() if line.strip()), "")
    return f", {first[:STDERR_CHARS]}" if first else ""


def _has_shape(output) -> bool:
    """최소한의 모양만 본다 (값 형식은 채점기·내보내기가 항목마다 본다)."""
    if not isinstance(output, dict) or not isinstance(output.get("is_contest"), dict):
        return False
    if not isinstance(output["is_contest"].get("value"), bool):
        return False
    return output["is_contest"]["value"] is False or isinstance(output.get("fields"), dict)


def version(claude: str) -> str | None:
    try:
        completed = subprocess.run([claude, "--version"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()[:60] or None
