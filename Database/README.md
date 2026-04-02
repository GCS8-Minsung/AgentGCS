# Database Setup

1. Supabase SQL Editor에서 [schema.sql](./schema.sql)을 실행합니다.
2. `auth.users` 신규 생성 시 `public.users`가 자동 동기화됩니다.
3. `tasks` 변경에 대한 선제 알림을 위해 Supabase Database Webhook을 생성해  
   `POST /api/webhooks/supabase/tasks` 로 전달합니다.

권장 Webhook 조건:
- 이벤트: `INSERT`, `UPDATE`
- 테이블: `public.tasks`
- 필터: `status != 'done'`
- 헤더: `x-webhook-secret: <SUPABASE_WEBHOOK_SECRET>`

