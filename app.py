from dotenv import load_dotenv

load_dotenv()

import joblib
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from handlers import (
    perform_ocr,
    check_should_review,
    check_is_sensitive,
    redact,
    get_outputs,
)
from fastapi import HTTPException
import json
from models import CommunityNoteRequest, AgentResponse, SupportedModelProvider
from middleware import RequestIDMiddleware  # Import the middleware
from context import request_id_var  # Import the context variable
from logger import StructuredLogger
from langfuse.decorators import observe, langfuse_context

langfuse_context.configure(
    enabled=True,
    # enabled=os.getenv("ENVIRONMENT") != "development",
)

logger = StructuredLogger("checkmate-ml-api")

app = FastAPI()

# Add the middleware to the application
app.add_middleware(RequestIDMiddleware)

embedding_model = SentenceTransformer("files/all-MiniLM-L6-v2")
L1_svc = joblib.load("files/L1_svc.joblib")


class ItemText(BaseModel):
    text: str


class ItemUrl(BaseModel):
    url: str


def cleanup(background_tasks: BackgroundTasks, log_message: str = None):
    if log_message:
        background_tasks.add_task(logger.info, log_message)
    background_tasks.add_task(langfuse_context.flush)


@app.post("/embed")
def get_embedding(item: ItemText, background_tasks: BackgroundTasks):
    logger.info("Processing embedding request", text=item.text[:100])
    embedding = embedding_model.encode(item.text)
    result = {"embedding": embedding.tolist()}
    cleanup(background_tasks, "Embedding generated successfully")
    return result


@app.post("/getL1Category")
def get_L1_category(item: ItemText, background_tasks: BackgroundTasks):
    logger.info("Processing L1 category request", text=item.text[:100])
    embedding = embedding_model.encode(item.text)
    prediction = L1_svc.predict(embedding.reshape(1, -1))[0]
    result = {"prediction": "irrelevant" if prediction == "trivial" else prediction}
    cleanup(background_tasks, "L1 category prediction complete")
    return result


@app.post("/sensitivity-filter")
def get_sensitivity(item: ItemText, background_tasks: BackgroundTasks):
    logger.info("Processing sensitivity filter request", text=item.text[:100])
    is_sensitive = check_is_sensitive(
        item.text, langfuse_observation_id=request_id_var.get()
    )
    result = {"is_sensitive": is_sensitive}
    cleanup(background_tasks, "Sensitivity check complete")
    return result


@app.post("/getNeedsChecking")
def get_needs_checking(item: ItemText, background_tasks: BackgroundTasks):
    logger.info("Processing needs checking request", text=item.text[:100])
    should_review = check_should_review(
        item.text, langfuse_observation_id=request_id_var.get()
    )
    result = {"needsChecking": should_review}
    cleanup(background_tasks, "Review check complete")
    return result


@app.post("/ocr-v2")
def get_ocr(item: ItemUrl, background_tasks: BackgroundTasks):
    logger.info("Processing OCR request", url=item.url)
    results = perform_ocr(item.url, langfuse_observation_id=request_id_var.get())
    if "extracted_message" in results and results["extracted_message"]:
        extracted_message = results["extracted_message"]
        logger.info(
            "Message extracted from image", extracted_text=extracted_message[:100]
        )
        prediction = get_L1_category(
            ItemText(text=extracted_message), background_tasks
        ).get("prediction", "unsure")
        results["prediction"] = prediction
    else:
        results["prediction"] = "unsure"
    cleanup(background_tasks, "OCR processing complete")
    return results


@app.post("/redact")
def get_redact(item: ItemText, background_tasks: BackgroundTasks):
    logger.info("Processing redaction request", text=item.text[:100])
    try:
        response, tokens_used = redact(
            item.text, langfuse_observation_id=request_id_var.get()
        )
        # set langfuse trace ID as request ID
        response_dict = json.loads(response)
        redacted_message = item.text
        for redaction in response_dict["redacted"]:
            redacted_text = redaction["text"]
            replacement = redaction["replaceWith"]
            redacted_message = redacted_message.replace(redacted_text, replacement)
        result = {
            "redacted": redacted_message,
            "original": item.text,
            "tokens_used": tokens_used,
            "reasoning": response_dict["reasoning"],
        }
        cleanup(background_tasks, "Redaction complete")
        return result
    except Exception as e:
        logger.error("Redaction failed", error=str(e))
        result = {
            "redacted": "",
            "original": item.text,
            "tokens_used": tokens_used or 0,
            "reasoning": "Error in redact function",
        }
        cleanup(background_tasks, "Redaction failed")
        return result


@app.post("/v2/getCommunityNote")
async def get_community_note_api_handler(
    request: CommunityNoteRequest,
    background_tasks: BackgroundTasks,
    provider: SupportedModelProvider = SupportedModelProvider.GEMINI,
) -> AgentResponse:
    logger.info(
        "Processing v2 community note request",
        provider=provider.value,
        has_text=bool(request.text),
        has_image=bool(request.image_url),
    )
    try:
        if request.text is None and request.image_url is None:
            raise HTTPException(
                status_code=400, detail="Either 'text' or 'image_url' must be provided."
            )
        if request.text is not None and request.image_url is not None:
            raise HTTPException(
                status_code=400,
                detail="Only one of 'text' or 'image_url' should be provided.",
            )
        result = await get_outputs(
            text=request.text,
            image_url=request.image_url,
            caption=request.caption,
            addPlanning=request.addPlanning,
            provider=provider,
            langfuse_observation_id=request_id_var.get(),  # set langfuse trace ID as request ID
        )
        cleanup(background_tasks, f"/getCommunityNote complete")
        return result

    except HTTPException as e:
        logger.error(
            "HTTP exception in note generation",
            status_code=e.status_code,
            detail=e.detail,
        )
        cleanup(background_tasks, "Gemini note failed to generate")
        raise e
    except Exception as e:
        logger.error("Unexpected error in note generation", error=str(e))
        cleanup(background_tasks, "Gemini note failed to generate")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8080, reload=True)
