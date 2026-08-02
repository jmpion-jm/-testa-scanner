import sys, json, py_compile

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

file_path = (data.get("tool_input") or {}).get("file_path", "")
if not file_path.endswith(".py"):
    sys.exit(0)

try:
    py_compile.compile(file_path, doraise=True)
except py_compile.PyCompileError as e:
    print(json.dumps({
        "decision": "block",
        "reason": f"문법 오류 감지: {file_path}\n{e.msg}",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": f"방금 수정한 {file_path}에 파이썬 문법 오류가 있습니다:\n{e.msg}\n실행하기 전에 고쳐야 합니다."
        }
    }))
except Exception:
    pass
