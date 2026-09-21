import RepositoryExplorer from "@/app/components/repository-explorer";

export default async function DisplayRepository({params}: {
    params: Promise<{ owner: string, repoName: string }>
}) {
    const {owner, repoName} = await params;

    return (
        <RepositoryExplorer owner={owner} repoName={repoName}/>
    );
}
