# AgentGCS - Autonomous Multi-Agent & Personal Workflow Automation

## Folder Structure

```txt
AgentGCS/
  Frontend/   # Next.js App Router + Tailwind + shadcn-style UI + Recharts
  Backend/    # FastAPI + WebSocket + LangGraph orchestration + AES-256 key manager
  Database/   # Supabase PostgreSQL schema + RLS policies
```

## Architecture Overview

1. **Frontend (Next.js)**
   - Google OAuth via Supabase Auth
   - Persona Control Radar(6축) 드래그 UI
   - Generative UI stream (spinner/toast/debate progress)
   - Kanban Board (Supabase `tasks` CRUD)
   - FastAPI와 REST + WebSocket 통신

2. **Backend (FastAPI)**
   - `SecurityManager`가 사용자 API 키를 AES-256-GCM으로 암호화 후 `user_keys` 저장
   - `DeepTaskOrchestrator`가 LangGraph 5인 페르소나 토론 워크플로우 실행
   - 실시간 상태를 WebSocket으로 프론트 전달
   - 결론 후 `notebooklm-mcp-cli` 후처리 호출 (환경 미지원 시 mock)
   - `PersonalAgentService`가 수동 트리거 + 마감 임박 webhook 토스트 생성

3. **Database (Supabase)**
   - `users`, `user_keys`, `tasks`, `agent_logs`
   - Row Level Security 정책 포함
   - vector 컬럼(`agent_logs.embedding`)으로 RAG 확장 여지 제공

## Mock Test Scenario Included

기본 과제로 아래 문장이 프론트에 자동 입력됩니다.

`볼류메트릭 디스플레이 3D 식물/크리처 렌더링 테스트에 대한 비즈니스 모델 구축`

사용자가 헥사곤 스탯을 조정하고 "멀티 에이전트 토론 시작"을 누르면:

1. `/api/agents/deep-task/start` 호출
2. 백엔드가 정보 탐색 노드 실행
3. 5개 페르소나 노드 순차 토론
4. 종합 결론 생성
5. NotebookLM 후처리 이벤트 전송
6. 프론트 Generative Feed에서 실시간으로 단계별 상태 확인

## Run (Local)

### 1) Backend

```bash
cd Backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

### 2) Frontend

```bash
cd Frontend
npm install
copy .env.example .env.local
npm run dev
```

### 3) Database

- Supabase SQL Editor에서 `Database/schema.sql` 실행
- 필요 시 `tasks` 변경 webhook을 FastAPI `POST /api/webhooks/supabase/tasks`에 연결

