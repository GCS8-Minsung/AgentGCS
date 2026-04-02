"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarClock, GripVertical } from "lucide-react";

import { createTask, fetchTasks, updateTask } from "@/lib/api";
import { TaskItem, TaskStatus } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

type Props = {
  userId: string;
};

const COLUMNS: { key: TaskStatus; label: string }[] = [
  { key: "todo", label: "Todo" },
  { key: "in_progress", label: "In Progress" },
  { key: "review", label: "Review" },
  { key: "done", label: "Done" }
];

export function KanbanBoard({ userId }: Props) {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [dueDate, setDueDate] = useState("");

  async function load() {
    setLoading(true);
    setErrorText(null);
    try {
      const response = await fetchTasks(userId);
      setTasks(response.items);
    } catch (error) {
      setTasks([]);
      setErrorText((error as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!userId) return;
    load();
  }, [userId]);

  async function addTask() {
    if (!title.trim()) return;
    try {
      const response = await createTask(userId, {
        title: title.trim(),
        due_date: dueDate || null,
        status: "todo"
      });
      setTasks((current) => [...current, response.item]);
      setTitle("");
      setDueDate("");
      setErrorText(null);
    } catch (error) {
      setErrorText((error as Error).message);
    }
  }

  async function moveTask(taskId: string, status: TaskStatus) {
    try {
      const response = await updateTask(userId, taskId, { status });
      setTasks((current) =>
        current.map((task) => (task.id === taskId ? { ...task, ...response.item } : task))
      );
      setErrorText(null);
    } catch (error) {
      setErrorText((error as Error).message);
    }
  }

  const grouped = useMemo(() => {
    const map: Record<TaskStatus, TaskItem[]> = {
      todo: [],
      in_progress: [],
      review: [],
      done: []
    };
    tasks.forEach((task) => map[task.status].push(task));
    return map;
  }, [tasks]);

  return (
    <Card className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[220px] flex-1 space-y-1">
          <CardTitle>Kanban Board</CardTitle>
          <CardDescription>Supabase `tasks` 테이블과 동기화되는 드래그 앤 드롭 보드입니다.</CardDescription>
          <Input
            placeholder="새 마일스톤/작업 제목"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div className="w-[170px]">
          <Input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
        </div>
        <Button variant="accent" onClick={addTask}>
          작업 추가
        </Button>
        <Button variant="secondary" onClick={load} disabled={loading}>
          새로고침
        </Button>
      </div>
      {errorText && (
        <p className="rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {errorText}
        </p>
      )}

      <section className="grid grid-cols-1 gap-3 xl:grid-cols-4">
        {COLUMNS.map((column) => (
          <div
            key={column.key}
            className="rounded-2xl border border-white/70 bg-white/40 p-3 backdrop-blur-xl"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              const taskId = event.dataTransfer.getData("text/task-id");
              if (taskId) moveTask(taskId, column.key);
            }}
          >
            <header className="mb-3 flex items-center justify-between">
              <h4 className="font-semibold text-gray-800">{column.label}</h4>
              <span className="rounded-full bg-white/80 px-2 py-0.5 text-xs font-medium text-orange-900/85">
                {grouped[column.key].length}
              </span>
            </header>
            <div className="space-y-2">
              {grouped[column.key].map((task) => (
                <article
                  key={task.id}
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData("text/task-id", task.id);
                  }}
                  className="cursor-grab rounded-2xl border border-white/70 bg-white p-3 text-sm shadow-sm"
                  style={{
                    borderColor: "rgba(255,255,255,0.8)",
                    background: "rgba(255,255,255,0.78)"
                  }}
                >
                  <div className="mb-2 flex items-center justify-between">
                    <p className="font-medium text-gray-800">{task.title}</p>
                    <GripVertical className="h-4 w-4 text-orange-900/35" />
                  </div>
                  {task.due_date && (
                    <p className="flex items-center gap-1 text-xs text-orange-900/70">
                      <CalendarClock className="h-3.5 w-3.5" />
                      {new Date(task.due_date).toLocaleDateString()}
                    </p>
                  )}
                </article>
              ))}
              {grouped[column.key].length === 0 && (
                <p className="rounded-lg border border-dashed border-white/70 p-3 text-xs text-orange-900/60">
                  이 컬럼에는 아직 작업이 없습니다.
                </p>
              )}
            </div>
          </div>
        ))}
      </section>
    </Card>
  );
}
