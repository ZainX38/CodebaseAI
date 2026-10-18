"use client"

// Asks questions about the repository and shows the answers as they are written.
// Each answer streams in through Server-Sent Events: status --> tokens --> done (with citations)

import { FormEvent, useEffect, useRef, useState } from "react";

import { streamChat } from "@/lib/api";
import type { Citation } from "@/lib/api";

import ChatMessage from "./ChatMessage";
import type { ChatEntry } from "./ChatMessage";

const MAX_QUESTION_CHARS = 1000;

type ChatPanelProps = {
    owner: string;
    repoName: string;
    onOpenCitation: (citation: Citation) => void;
}

export default function ChatPanel({owner, repoName, onOpenCitation}: ChatPanelProps) {
    const [entries, setEntries] = useState<ChatEntry[]>([]);
    const [question, setQuestion] = useState("");
    const abortController = useRef<AbortController | null>(null);
    const messages = useRef<HTMLDivElement>(null);
    const followNewText = useRef(true);
    const nextId = useRef(1);

    const isStreaming = entries.some((entry) => entry.isStreaming);

    // Keeps the newest text in view while the answer grows. Only the chat's own scroll
    // area moves (scrollIntoView would also scroll the whole page), and only if the user
    // hasn't scrolled up to read an earlier answer
    useEffect(() => {
        const container = messages.current;
        if (container && entries.length > 0 && followNewText.current) {
            container.scrollTop = container.scrollHeight;
        }
    }, [entries]);

    function handleScroll() {
        const container = messages.current;
        if (container) {
            followNewText.current = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
        }
    }

    // Leaving the page stops the answer, so the backend doesn't keep generating it
    useEffect(() => () => abortController.current?.abort(), []);

    // Changes one entry without touching the others
    function updateEntry(id: number, change: (entry: ChatEntry) => Partial<ChatEntry>) {
        setEntries((current) => current.map((entry) => (entry.id === id ? {...entry, ...change(entry)} : entry)));
    }

    async function handleSubmit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        const trimmed = question.trim();
        if (!trimmed || isStreaming) return;

        const id = nextId.current++;
        setEntries((current) => [...current, {
            id, question: trimmed, answer: "", stage: "", citations: [], rejectedCitations: [], error: "", isStreaming: true,
        }]);
        setQuestion("");
        // A new question always scrolls to the bottom to show its answer
        followNewText.current = true;

        const controller = new AbortController();
        abortController.current = controller;

        try {
            await streamChat(owner, repoName, trimmed, {
                onStatus: (stage) => updateEntry(id, () => ({stage})),
                onToken: (text) => updateEntry(id, (entry) => ({answer: entry.answer + text})),
                // The final answer replaces the streamed text, so both always match
                onDone: (result) => updateEntry(id, () => ({
                    answer: result.answer,
                    citations: result.citations,
                    rejectedCitations: result.rejected_citations,
                    isStreaming: false,
                })),
                onError: (detail) => updateEntry(id, () => ({error: detail, isStreaming: false})),
            }, controller.signal);
        } catch (error) {
            const stopped = error instanceof DOMException && error.name === "AbortError";
            updateEntry(id, () => ({
                error: stopped ? "Stopped." : error instanceof Error ? error.message : "The question could not be sent",
                isStreaming: false,
            }));
        }
    }

    return (
        <aside className="flex h-full min-h-96 min-w-0 flex-col border-t border-slate-300 bg-white xl:min-h-0 xl:border-t-0 xl:border-l">
            <div className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 px-4 py-4">
                <span className="flex size-9 items-center justify-center rounded-lg border border-sky-200 bg-sky-50 text-sky-700">
                    <svg aria-hidden="true" className="size-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
                    </svg>
                </span>
                <div>
                    <p className="text-sm font-semibold text-slate-800">Ask about this code</p>
                    <p className="text-xs text-slate-500">Answers cite the exact lines they use</p>
                </div>
            </div>

            <div ref={messages} onScroll={handleScroll} className="min-h-0 flex-1 overflow-auto p-4">
                {entries.length === 0 && (
                    <p className="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm leading-6 text-slate-500">
                        Index the repository, then ask something like
                        <span className="mt-1 block font-mono text-xs text-slate-600">How does Session.send handle redirects?</span>
                    </p>
                )}
                {/* aria-live lets screen readers announce answers as they arrive */}
                <ul className="space-y-6" aria-live="polite">
                    {entries.map((entry) => (
                        <ChatMessage key={entry.id} entry={entry} onOpenCitation={onOpenCitation} />
                    ))}
                </ul>
            </div>

            <form onSubmit={handleSubmit} className="border-t border-slate-200 bg-slate-50 p-3">
                <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    onKeyDown={(event) => {
                        // Enter sends, Shift+Enter adds a new line
                        if (event.key === "Enter" && !event.shiftKey) {
                            event.preventDefault();
                            event.currentTarget.form?.requestSubmit();
                        }
                    }}
                    maxLength={MAX_QUESTION_CHARS}
                    rows={2}
                    aria-label="Question about the code"
                    placeholder="Ask a question about the code..."
                    className="w-full resize-none rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-sky-400 focus:outline-none focus:ring-2 focus:ring-sky-200"
                />
                <div className="mt-2 flex items-center justify-end gap-2">
                    {isStreaming && (
                        <button
                            type="button"
                            onClick={() => abortController.current?.abort()}
                            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-white"
                        >
                            Stop
                        </button>
                    )}
                    <button
                        type="submit"
                        disabled={isStreaming || !question.trim()}
                        className="rounded-md bg-sky-600 px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        Ask
                    </button>
                </div>
            </form>
        </aside>
    );
}
