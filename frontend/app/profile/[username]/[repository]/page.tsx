import {auth0} from "@/lib/auth0"

export default async function DisplayRepository({params}: {
    params: Promise<{ username: string, repository: string }>
}) {

    const { username, repository } = await params;

    return (
        <p>{`${username} and ${repository}`}</p>
    );
}