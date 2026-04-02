# Backend (FastAPI)

## Core Endpoints

- `POST /api/keys`  
  사용자 API 키를 AES-256-GCM으로 암호화하여 `user_keys` 저장
- `GET /api/tasks` / `POST /api/tasks` / `PATCH /api/tasks/{id}`  
  Kanban task CRUD
- `POST /api/agents/deep-task/start`  
  LangGraph 5인 토론 워크플로우 시작 (백그라운드 실행)
- `POST /api/agents/personal/trigger`  
  개인 업무 에이전트 수동 트리거
- `POST /api/webhooks/supabase/tasks`  
  마감 임박 이벤트 처리 후 실시간 Toast 알림 브로드캐스트
- `WS /ws/agents?user_id=<uuid>`  
  실시간 상태 스트리밍

## Runtime Notes

- 기본적으로 `use_mock=true`로 작동하여 외부 API 없이도 흐름을 검증할 수 있습니다.
- `CLAUDE_API_KEY`를 세팅하고 요청 시 `use_mock=false`를 보내면 Claude 호출을 시도합니다.
- 권장 Python 버전은 `3.11 ~ 3.13`입니다. (일부 LangChain 계열 라이브러리는 3.14에서 경고가 표시될 수 있음)

