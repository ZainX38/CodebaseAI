import type { Citation } from "@/lib/api";

type CitationListProps = {
    citations: Citation[];
    onOpenCitation: (citation: Citation) => void;
}

// Each citation opens the file in the explorer at the indexed commit with the lines
// highlighted. The small arrow opens the same lines on GitHub instead
export default function CitationList({citations, onOpenCitation}: CitationListProps) {
    return (
        <ul className="mt-3 space-y-1.5">
            {citations.map((citation) => {
                const fileName = citation.path.split("/").pop();
                return (
                    <li key={citation.id} className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => onOpenCitation(citation)}
                            className="flex min-w-0 flex-1 items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-left text-xs transition-colors hover:border-sky-300 hover:bg-sky-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                            title={`${citation.path}:${citation.start_line}-${citation.end_line}`}
                        >
                            <span className="shrink-0 rounded bg-sky-100 px-1.5 py-0.5 font-mono font-semibold text-sky-800">
                                {citation.id}
                            </span>
                            <span className="truncate font-mono text-slate-700">
                                {fileName}:{citation.start_line}-{citation.end_line}
                            </span>
                            {citation.symbol && (
                                <span className="truncate text-slate-500">{citation.symbol}</span>
                            )}
                        </button>
                        <a
                            href={citation.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="shrink-0 rounded-md px-1.5 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                            aria-label={`Open ${citation.path} lines ${citation.start_line}-${citation.end_line} on GitHub`}
                        >
                            ↗
                        </a>
                    </li>
                );
            })}
        </ul>
    );
}
