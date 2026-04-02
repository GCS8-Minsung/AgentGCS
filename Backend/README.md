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
- `POST /api/agents/personal/school-action`  
  개인 업무 에이전트가 교내 API 액션(회의실 조회/예약)을 즉시 실행
- `GET /api/school/meeting-rooms`  
  회의실 목록 조회
- `GET /api/school/meeting-rooms/{room_id}/reservations`  
  회의실 예약 목록 조회
- `POST /api/school/meeting-rooms/reservations`  
  회의실 예약 생성
- `DELETE /api/school/meeting-rooms/reservations/{reservation_id}`  
  회의실 예약 취소
- `GET|POST|PUT /api/school/daily-snippets`  
  일간 스니펫 조회/작성/수정
- `GET|POST|PUT /api/school/weekly-snippets`  
  주간 스니펫 조회/작성/수정
- `POST /api/webhooks/supabase/tasks`  
  마감 임박 이벤트 처리 후 실시간 Toast 알림 브로드캐스트
- `WS /ws/agents?user_id=<uuid>`  
  실시간 상태 스트리밍

## Runtime Notes

- 기본적으로 `use_mock=true`로 작동하여 외부 API 없이도 흐름을 검증할 수 있습니다.
- `CLAUDE_API_KEY`를 세팅하고 요청 시 `use_mock=false`를 보내면 Claude 호출을 시도합니다.
- 교내 API 연동은 `user_keys`에 `key_name=school_api_token`으로 토큰 저장 후 동작합니다.
- 권장 Python 버전은 `3.11 ~ 3.13`입니다. (일부 LangChain 계열 라이브러리는 3.14에서 경고가 표시될 수 있음)
