import type { RepositoryFile } from "@/lib/api";

type FileViewerProps = {
    selectedFile: RepositoryFile | null;
    isLoading: boolean;
    error: string;
}

export default function FileViewer({selectedFile, isLoading, error}: FileViewerProps) {
    return (
        <section className="min-w-0 overflow-auto bg-gray-50">
            {isLoading && <p className="p-4 text-sm text-gray-500">Loading file...</p>}
            {error && <p className="p-4 text-sm text-red-600">{error}</p>}
            {!isLoading && !error && !selectedFile && (
                <p className="p-4 text-sm text-gray-500">Select a file to view its contents.</p>
            )}
            {selectedFile && !isLoading && (
                <>
                    <div className="border-b border-gray-200 bg-white px-4 py-2 text-sm text-gray-600">
                        {selectedFile.path}
                    </div>
                    <pre className="min-w-max p-4 font-mono text-sm leading-6 whitespace-pre">
                        <code>{selectedFile.content}</code>
                    </pre>
                </>
            )}
        </section>
    );
}
