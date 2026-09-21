import type { RepositoryFile } from "@/lib/api";

type FileViewerProps = {
    selectedFile: RepositoryFile | null;
    isLoading: boolean;
    error: string;
}

export default function FileViewer({selectedFile, isLoading, error}: FileViewerProps) {
    return (
        <section className="flex min-h-96 min-w-0 flex-col overflow-hidden border-t-2 border-sky-500 bg-slate-950 md:border-t-0 md:border-l-2">
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
                    </div>
                    <div className="min-h-0 flex-1 overflow-auto bg-slate-950">
                        <pre className="min-h-full min-w-max p-6 font-mono text-sm leading-7 text-slate-200 whitespace-pre selection:bg-sky-600 selection:text-white sm:p-8">
                            <code className="block">{selectedFile.content}</code>
                        </pre>
                    </div>
                </div>
            )}
        </section>
    );
}
