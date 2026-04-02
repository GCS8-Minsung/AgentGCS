-- AgentGCS Supabase Schema + RLS
-- Apply in Supabase SQL Editor as a privileged role.

create extension if not exists "pgcrypto";
create extension if not exists "vector";

-- 1) users
create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null unique,
  full_name text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Keep profile synced when a new auth user is created.
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.users (id, email, full_name, avatar_url)
  values (
    new.id,
    coalesce(new.email, ''),
    coalesce(new.raw_user_meta_data ->> 'full_name', ''),
    new.raw_user_meta_data ->> 'avatar_url'
  )
  on conflict (id) do update
    set email = excluded.email,
        full_name = excluded.full_name,
        avatar_url = excluded.avatar_url,
        updated_at = now();
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_auth_user();

-- 2) user_keys (encrypted API key vault)
create table if not exists public.user_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  key_name text not null,
  encrypted_value text not null,
  nonce text not null,
  key_version smallint not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, key_name)
);

-- 3) tasks (kanban board)
create table if not exists public.tasks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text not null,
  description text,
  status text not null default 'todo',
  due_date date,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint tasks_status_check check (status in ('todo', 'in_progress', 'review', 'done'))
);
create index if not exists idx_tasks_user_status on public.tasks(user_id, status);
create index if not exists idx_tasks_due_date on public.tasks(due_date);

-- 4) agent_logs (RAG + feedback)
create table if not exists public.agent_logs (
  id bigint generated always as identity primary key,
  run_id text not null,
  user_id uuid not null references public.users(id) on delete cascade,
  task text not null,
  persona_stats jsonb not null default '{}'::jsonb,
  arguments jsonb not null default '{}'::jsonb,
  final_summary text not null,
  sources jsonb not null default '[]'::jsonb,
  feedback_score smallint,
  embedding vector(1536),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint agent_logs_feedback_check check (feedback_score is null or feedback_score between 1 and 5)
);
create index if not exists idx_agent_logs_user_created on public.agent_logs(user_id, created_at desc);
create index if not exists idx_agent_logs_run_id on public.agent_logs(run_id);

-- shared updated_at trigger
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_users_set_updated_at on public.users;
create trigger trg_users_set_updated_at
before update on public.users
for each row execute function public.set_updated_at();

drop trigger if exists trg_user_keys_set_updated_at on public.user_keys;
create trigger trg_user_keys_set_updated_at
before update on public.user_keys
for each row execute function public.set_updated_at();

drop trigger if exists trg_tasks_set_updated_at on public.tasks;
create trigger trg_tasks_set_updated_at
before update on public.tasks
for each row execute function public.set_updated_at();

drop trigger if exists trg_agent_logs_set_updated_at on public.agent_logs;
create trigger trg_agent_logs_set_updated_at
before update on public.agent_logs
for each row execute function public.set_updated_at();

-- RLS
alter table public.users enable row level security;
alter table public.user_keys enable row level security;
alter table public.tasks enable row level security;
alter table public.agent_logs enable row level security;

-- users
drop policy if exists "users_select_own" on public.users;
create policy "users_select_own"
on public.users
for select
using (auth.uid() = id);

drop policy if exists "users_insert_own" on public.users;
create policy "users_insert_own"
on public.users
for insert
with check (auth.uid() = id);

drop policy if exists "users_update_own" on public.users;
create policy "users_update_own"
on public.users
for update
using (auth.uid() = id)
with check (auth.uid() = id);

-- user_keys
drop policy if exists "keys_select_own" on public.user_keys;
create policy "keys_select_own"
on public.user_keys
for select
using (auth.uid() = user_id);

drop policy if exists "keys_insert_own" on public.user_keys;
create policy "keys_insert_own"
on public.user_keys
for insert
with check (auth.uid() = user_id);

drop policy if exists "keys_update_own" on public.user_keys;
create policy "keys_update_own"
on public.user_keys
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "keys_delete_own" on public.user_keys;
create policy "keys_delete_own"
on public.user_keys
for delete
using (auth.uid() = user_id);

-- tasks
drop policy if exists "tasks_select_own" on public.tasks;
create policy "tasks_select_own"
on public.tasks
for select
using (auth.uid() = user_id);

drop policy if exists "tasks_insert_own" on public.tasks;
create policy "tasks_insert_own"
on public.tasks
for insert
with check (auth.uid() = user_id);

drop policy if exists "tasks_update_own" on public.tasks;
create policy "tasks_update_own"
on public.tasks
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "tasks_delete_own" on public.tasks;
create policy "tasks_delete_own"
on public.tasks
for delete
using (auth.uid() = user_id);

-- agent_logs
drop policy if exists "agent_logs_select_own" on public.agent_logs;
create policy "agent_logs_select_own"
on public.agent_logs
for select
using (auth.uid() = user_id);

drop policy if exists "agent_logs_insert_own" on public.agent_logs;
create policy "agent_logs_insert_own"
on public.agent_logs
for insert
with check (auth.uid() = user_id);

drop policy if exists "agent_logs_update_own" on public.agent_logs;
create policy "agent_logs_update_own"
on public.agent_logs
for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

-- Optional event function for proactive workflow triggers.
-- Connect this function to an Edge Function / DB webhook pipeline.
create or replace function public.notify_task_due_change()
returns trigger
language plpgsql
as $$
begin
  -- no-op placeholder. Supabase Database Webhooks can observe INSERT/UPDATE
  -- on public.tasks and post payload to FastAPI /api/webhooks/supabase/tasks.
  return new;
end;
$$;

