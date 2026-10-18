import type { Citation } from "@/lib/api";

import CitationList from "./CitationList";

export type ChatEntry = {
    id: number;
    question: string;
    answer: string;
    stage: string;
    citations: Citation[];
    rejectedCitations: string[];
    error: string;
    isStreaming: boolean;
}

const STAGE_LABELS: Record<string, string> = {
    retrieving: "Searching the code...",
    generating: "Writing the answer...",
    validating: "Checking the citations...",
};

type ChatMessageProps = {
    entry: ChatEntry;
    onOpenCitation: (citation: Citation) => void;
}

export default function ChatMessage({entry, onOpenCitation}: ChatMessageProps) {
    const isFinished = !entry.isStreaming && !entry.error;

    return (
        <li className="space-y-3">
            <div className="ml-8 rounded-xl rounded-br-sm bg-slate-900 px-4 py-3 text-sm leading-6 text-white">
                {entry.question}
            </div>

            <div className="mr-4 rounded-xl rounded-bl-sm border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-800">
                {entry.isStreaming && !entry.answer && (
                    <p className="flex items-center gap-2 text-slate-500">
                        <span className="size-2 animate-pulse rounded-full bg-sky-500"></span>
                        {STAGE_LABELS[entry.stage] ?? "Starting..."}
                    </p>
                )}

                {/* Plain text only: the answer comes from a model, so it is never rendered as HTML */}
                {entry.answer && <p className="whitespace-pre-wrap">{entry.answer}</p>}

                {entry.error && (
                    <p className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-red-700">{entry.error}</p>
                )}

                {isFinished && entry.citations.length > 0 && (
                    <CitationList citations={entry.citations} onOpenCitation={onOpenCitation} />
                )}

                {isFinished && entry.citations.length === 0 && (
                    <p className="mt-3 text-xs text-slate-500">
                        No citations: this answer isn&apos;t backed by any of the code that was shown to the model.
                    </p>
                )}

                {isFinished && entry.rejectedCitations.length > 0 && (
                    <p className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        The model also cited sources it was never given, so they were removed:{" "}
                        <span className="font-mono">{entry.rejectedCitations.join(", ")}</span>
                    </p>
                )}
            </div>
        </li>
    );
}
