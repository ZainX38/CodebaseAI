import { useEffect, useRef } from "react";

import type { RepositoryFile } from "@/lib/api";

// The lines a citation points at (1-based and inclusive, like GitHub)
export type LineRange = {
    start: number;
    end: number;
}

type FileViewerProps = {
    selectedFile: RepositoryFile | null;
    isLoading: boolean;
    error: string;
    highlight?: LineRange | null;
}

export default function FileViewer({selectedFile, isLoading, error, highlight}: FileViewerProps) {
    const codeArea = useRef<HTMLDivElement>(null);
    const firstHighlightedLine = useRef<HTMLDivElement>(null);

    // When a citation is opened, the code area (not the whole page) scrolls so the first
    // cited line is in the middle of it
    useEffect(() => {
        const container = codeArea.current;
        const line = firstHighlightedLine.current;
        if (container && line) {
            container.scrollTo({top: line.offsetTop - container.clientHeight / 2, behavior: "smooth"});
        }
    }, [selectedFile, highlight]);

    // Lines are split the same way as the backend's chunking (\r\n, \r or \n), so the
    // line numbers always match the citations
    const lines = selectedFile ? selectedFile.content.split(/\r\n|\r|\n/) : [];
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();

    return (
        // Below xl the viewer has its own height limit, so a long file scrolls inside it
        // instead of pushing the chat thousands of pixels down the page
        <section className="flex h-[70vh] min-h-96 min-w-0 flex-col overflow-hidden border-t-2 border-sky-500 bg-slate-950 md:border-t-0 md:border-l-2 xl:h-auto">
            {isLoading && (
                <div className="flex flex-1 items-center justify-center p-10">
                    <div className="text-center">
                        <span className="mx-auto mb-4 block size-3 rounded-full bg-sky-400"></span>
                        <p className="text-sm font-medium text-slate-300">Loading file...</p>
                    </div>
                </div>
            )}
            {error && (
                <div className="flex flex-1 items-start justify-center p-6 sm:p-10">
                    <div className="w-full max-w-xl rounded-lg border border-red-900 bg-red-950/50 px-5 py-4">
                        <p className="mb-1 text-sm font-semibold text-red-200">Unable to display file</p>
                        <p className="text-sm leading-6 text-red-300">{error}</p>
                    </div>
                </div>
            )}
            {!isLoading && !error && !selectedFile && (
                <div className="flex flex-1 items-center justify-center p-10">
                    <div className="max-w-sm text-center">
                        <span className="mx-auto mb-5 flex size-14 items-center justify-center rounded-xl border border-slate-700 bg-slate-900 text-sky-400">
                            <svg
                                aria-hidden="true"
                                className="size-6"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="1.5"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25H6.75A2.25 2.25 0 0 0 4.5 4.5v15a2.25 2.25 0 0 0 2.25 2.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-12L14.25 2.25Z" />
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25V7.5h5.25M8.25 13.5h7.5M8.25 17.25h5.25" />
                            </svg>
                        </span>
                        <p className="mb-2 text-base font-semibold text-slate-100">No file selected</p>
                        <p className="text-sm leading-6 text-slate-400">Select a file to view its contents.</p>
                    </div>
                </div>
            )}
            {selectedFile && !isLoading && (
                <div className="flex min-h-0 flex-1 flex-col">
                    <div className="flex items-center gap-4 border-b border-slate-700 bg-slate-900 px-5 py-4 sm:px-6">
                        <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-sky-500/30 bg-sky-400/10 text-sky-400">
                            <svg
                                aria-hidden="true"
                                className="size-5"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth="1.5"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25H6.75A2.25 2.25 0 0 0 4.5 4.5v15a2.25 2.25 0 0 0 2.25 2.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-12L14.25 2.25Z" />
                                <path strokeLinecap="round" strokeLinejoin="round" d="M14.25 2.25V7.5h5.25" />
                            </svg>
                        </span>
                        <div className="min-w-0">
                            <p className="mb-1 text-xs font-semibold tracking-widest text-sky-400 uppercase">
                                Selected file
                            </p>
                            <p className="truncate font-mono text-sm font-medium text-slate-100" title={selectedFile.path}>
                                {selectedFile.path}
                            </p>
                        </div>
                        {selectedFile.ref && (
                            // Cited files are shown at the commit that was indexed, which can be
                            // older than the branch the explorer shows everywhere else
                            <span
                                className="ml-auto shrink-0 rounded-md border border-amber-400/40 bg-amber-400/10 px-2 py-1 font-mono text-xs text-amber-300"
                                title={`Shown at the indexed commit ${selectedFile.ref}`}
                            >
                                commit {selectedFile.ref.slice(0, 7)}
                            </span>
                        )}
                    </div>
                    {/* relative: offsetTop of a line is measured from this scroll area */}
                    <div ref={codeArea} className="relative min-h-0 flex-1 overflow-auto bg-slate-950">
                        <pre className="min-h-full min-w-max py-6 font-mono text-sm leading-7 text-slate-200 whitespace-pre selection:bg-sky-600 selection:text-white sm:py-8">
                            <code className="block">
                                {lines.map((line, index) => {
                                    const lineNumber = index + 1;
                                    const isHighlighted = highlight !== null && highlight !== undefined
                                        && lineNumber >= highlight.start && lineNumber <= highlight.end;

                                    return (
                                        <div
                                            key={lineNumber}
                                            ref={isHighlighted && lineNumber === highlight?.start ? firstHighlightedLine : undefined}
                                            className={`flex pr-6 sm:pr-8 ${isHighlighted ? "bg-sky-500/15 shadow-[inset_3px_0_0_0_rgb(56,189,248)]" : ""}`}
                                        >
                                            <span className="w-14 shrink-0 select-none pr-4 text-right text-slate-600">{lineNumber}</span>
                                            {/* An empty line still needs some content to keep its height */}
                                            <span>{line || " "}</span>
                                        </div>
                                    );
                                })}
                            </code>
                        </pre>
                    </div>
                </div>
            )}
        </section>
    );
}
