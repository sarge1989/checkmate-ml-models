from google.genai import types

from clients.gemini import gemini_client
from typing import Union, List
from utils.gemini_utils import generate_image_parts, generate_text_parts
import json


summary_prompt = """You are a model powering CheckMate, a product that allows users based in Singapore to send in dubious content they aren't sure whether to trust, and checks such content on their behalf.

Such content is sent via WhatsApp, and can be a text message or an image message.

Given the following inputs:
- content submitted by the user
- long-form report generated by a fact-checking model

Your job is to summarise the report into an X-style community note of around 50-100 words. This should be clear.

The note should also be written with the assumption that users have short attention spans. Thus, it should start with a clear statement that gives the user clarity on the message. For example (but not limited to):
[For messages that are clearly scams, i.e. attempts to obtain money/personal information via deception] - 🚨 This is a scam
[For messages indicative of illegality, e.g. unlicensed moneylending, gambling] - 🚨 This is suspicious
[For messages that are clearly falsehoods] - ❌ This is largely untrue
[For messages that are otherwise harmful] - 🛑 This is likely harmful
[For messages that are from legitimate sources] - ✅ This a legitimate <something>
[For information/commentary that is broadly accurate] - ✅ This is largely true
[For information/commentary that is misleading or unbalanced] - ⚠️ Take this with a pinch of salt
[For content that otherwise warrants caution] - ⚠️ Be cautious

A good note would start with a clear statement like the above, and then justify it while summarising the key points of the report. There's no need to describe/summary what's in the message itself.
"""

summary_response_schema = {
    "type": "OBJECT",
    "properties": {
        "community_note": {
            "type": "STRING",
            "description": "The community note you generated, which should start with a clear statement, followed by a concise elaboration.",
        }
    },
}


def summarise_report_factory(
    input_text: Union[str, None] = None,
    input_image_url: Union[str, None] = None,
    input_caption: Union[str, None] = None,
):
    """
    Factory function that returns a summarise_report function with input_text, input_image_url, input_caption pre-set.
    """

    async def summarise_report(report: str):
        """
        Summarise the report (with pre-set inputs for text, image URL, or caption).
        """
        if input_text is not None and input_image_url is not None:
            raise ValueError(
                "Only one of input_text or input_image_url should be provided"
            )
        if input_text:
            parts = generate_text_parts(input_text)
        elif input_image_url:
            parts = generate_image_parts(input_image_url, input_caption)
        parts.append(types.Part.from_text(f"***Report***: {report}\n****End Report***"))
        messages = [types.Content(parts=parts, role="user")]
        try:
            response = gemini_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=messages,
                config=types.GenerateContentConfig(
                    systemInstruction=summary_prompt,
                    response_mime_type="application/json",
                    response_schema=summary_response_schema,
                    temperature=0.1,
                ),
            )
        except Exception as e:
            print(f"Error in generation: {e}")
            return {"error": str(e), "success": False}
        try:
            response_json = json.loads(response.candidates[0].content.parts[0].text)
        except Exception as e:
            print(f"Error: {e}")
            return {"success": False, "error": str(e)}
        if not isinstance(response_json, dict):
            print(f"response_json: {response_json}")
            return {
                "success": False,
                "error": "Response from summariser is not a dictionary",
            }
        if response_json.get("community_note"):
            return {"community_note": response_json["community_note"], "success": True}
        else:
            return {"success": False, "error": "No community note generated"}

    return summarise_report


summarise_report_definition = dict(
    name="summarise_report",
    description="Given a long-form report, and the text or image message the user originally sent in, summarises the report into an X-style community note of around 50-100 words.",
    parameters={
        "type": "OBJECT",
        "properties": {
            "report": {
                "type": "STRING",
                "description": "The long-form report to summarise.",
            },
        },
        "required": ["reasoning", "intent"],
    },
)

summarise_report_tool = {
    "function": None,
    "definition": summarise_report_definition,
}
