# tools/review_report.py

from google.genai import types

from clients.gemini import gemini_client
import json

system_prompt_review = """#Instructions

You are playing the role of an editor for a credibility/fact-checking service.

You will be provided with a report is written for the public, on a piece of information that has been submitted.

Your role is to review the submission for:

- clarity
- presence of logical errors or inconsistencies
- credibility of sources used

Points to note:
- Do not nitpick, work on the assumption that the drafter is competent
- You have no ability to do your own research. Do not attempt to use your own knowledge, assume that the facts within the note are correct."""

response_schema = {
    "type": "OBJECT",
    "properties": {
        "feedback": {
            "type": "STRING",
            "description": "Your feedback on the report, if any",
        },
        "passedReview": {
            "type": "BOOLEAN",
            "description": "A boolean indicating whether the item passed the review",
        },
    },
}


async def submit_report_for_review(
    report, sources, isControversial, isVideo, isAccessBlocked
):
    formatted_sources = "\n- ".join(sources) if sources else "<None>"
    if sources:
        formatted_sources = (
            "- " + formatted_sources
        )  # Add the initial '- ' if sources are present
    user_prompt = f"Report: {report}\n*****\nSources:{formatted_sources}"
    response = gemini_client.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=[types.Part(text=user_prompt)],
        config=types.GenerateContentConfig(
            systemInstruction=system_prompt_review,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.5,
        ),
    )
    return {"result": json.loads(response.candidates[0].content.parts[0].text)}


review_report_definition = dict(
    name="submit_report_for_review",
    description="Submits a report, which concludes the task.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "report": {
                "type": "STRING",
                "description": "The content of the report. This should enough context for readers to stay safe and informed. Try and be succinct.",
            },
            "sources": {
                "type": "ARRAY",
                "items": {
                    "type": "STRING",
                    "description": "A link from which you sourced content for your report.",
                },
                "description": "A list of links from which your report is based. Avoid including the original link sent in for checking as that is obvious.",
            },
            "isControversial": {
                "type": "BOOLEAN",
                "description": "True if the content contains political or religious viewpoints likely to be divisive.",
            },
            "isVideo": {
                "type": "BOOLEAN",
                "description": "True if the content or URL points to a video (e.g., YouTube, TikTok, Instagram Reels, Facebook videos).",
            },
            "isAccessBlocked": {
                "type": "BOOLEAN",
                "description": "True if the content or URL is inaccessible/removed/blocked. An example is being led to a login page instead of post content.",
            },
        },
        "required": [
            "report",
            "sources",
            "isControversial",
            "isVideo",
            "isAccessBlocked",
        ],
    },
)

review_report_tool = {
    "function": submit_report_for_review,
    "definition": review_report_definition,
}
