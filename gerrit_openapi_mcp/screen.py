"""Compose generated read tools into data for a change screen."""

from fastmcp import Client

LIST_CHECKS = "getChangesChangeIdRevisionsRevisionIdChecks"
GET_CHECK = "getChangesChangeIdRevisionsRevisionIdChecksCheckId"


async def change_screen_data(
    core: Client, checks: Client, change_id: str, revision: str | None = None
) -> dict:
    detail = await core.call_tool(
        "getChangesChangeIdDetail",
        {
            "change-id": change_id,
            "o": ["CURRENT_REVISION", "DETAILED_LABELS"],
        },
    )
    change = detail.structured_content
    if not isinstance(change, dict):
        raise TypeError("Expected structured change detail")
    revision = revision or change.get("current_revision")
    if not revision:
        raise ValueError("Change detail did not include current_revision")
    args = {"change-id": change_id, "revision-id": revision}
    response = await checks.call_tool(LIST_CHECKS, args)
    runs = (response.structured_content or {}).get("result")
    if not isinstance(runs, list):
        raise TypeError("Expected a list of checks")
    detailed = []
    for run in runs:
        # ListChecks' generated spec currently omits its -o parameter. The
        # individual-check tool declares it, so request metadata there instead.
        response = await checks.call_tool(
            GET_CHECK,
            {
                **args,
                "check-id": run["checker_uuid"],
                "o": ["CHECKER"],
            },
        )
        detailed.append(response.structured_content)
    return {"change": change, "revision": revision, "checks": detailed}
