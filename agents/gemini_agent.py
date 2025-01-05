# /agents/gemini_agent.py:

from .abstract import FactCheckingAgentBaseClass
from typing import Union, List
from google.genai import types
from utils.gemini_utils import get_image_part, generate_image_parts, generate_text_parts
import asyncio
import time
from tools import summarise_report_factory


class GeminiAgent(FactCheckingAgentBaseClass):

    def __init__(
        self,
        client,
        tool_list: list,
        system_prompt: str,
        include_planning_step: bool = True,
        temperature: float = 0.2,
    ):
        """Initializes the FactCheckingAgentBaseClass with a list of tools.

        Each tool should be a dictionary with two keys "function" and "definition".

        The former will hold the function itself, and the latter an openAPI specification dictionary
        """
        self.include_planning_step = include_planning_step
        if not self.include_planning_step:
            tool_list = [
                tool
                for tool in tool_list
                if tool["function"].__name__ != "plan_next_step"
            ]
        super().__init__(client, tool_list, system_prompt, temperature)
        self.function_tool = types.Tool(function_declarations=self.function_definitions)

    @staticmethod
    def flatten_and_organise(
        list_of_parts: List[Union[types.Part, List[types.Part]]]
    ) -> List[types.Part]:
        """
        Flattens a list of parts, which may contain individual `types.Part` objects or lists of `types.Part`,
        and returns a single list with parts having `function_response` first, followed by the rest.

        Args:
            list_of_parts: A list containing `types.Part` objects or lists of `types.Part`.

        Returns:
            A single list of `types.Part` objects, ordered with those having a non-None `function_response` first.
        """
        flattened_results = [
            item
            for sublist in list_of_parts
            for item in (sublist if isinstance(sublist, list) else [sublist])
        ]
        function_responses = [
            item for item in flattened_results if item.function_response is not None
        ]
        other_responses = [
            item for item in flattened_results if item.function_response is None
        ]
        return function_responses + other_responses

    @staticmethod
    def process_trace(traces):
        """Utility method to process the parts returned by the Gemini model into a readable trace"""
        log_message = []
        for trace in traces:
            if trace.role == "user":
                log_message.extend(GeminiAgent._process_user_trace(trace))
            else:
                log_message.extend(GeminiAgent._process_model_trace(trace))
        return log_message

    @staticmethod
    def _process_user_trace(trace):
        """Utility method to process the user parts of the trace"""
        responses = []
        for part in trace.parts:
            if part.function_response is not None:
                response = {
                    "role": "user",
                    "name": part.function_response.name,
                    "response": part.function_response.response,
                }
            elif part.text is not None:
                response = {"role": "user", "text": part.text}
            elif part.file_data is not None:
                response = {"role": "user", "text": "<IMAGE_DATA>"}
            elif part.inline_data is not None:
                response = {"role": "user", "text": "<INLINE_DATA>"}
            responses.append(response)
        return responses

    @staticmethod
    def _process_model_trace(trace):
        """Utility method to process the model parts of the trace"""
        responses = []
        for part in trace.parts:
            try:
                response = {
                    "role": "model",
                    "name": part.function_call.name,
                    "response": part.function_call.args,
                }
                responses.append(response)
            except Exception as e:
                print(f"An error occurred {e}")
                print(part)
        return responses

    async def call_function(
        self, function_call: types.FunctionCall
    ) -> Union[types.Part, List[types.Part]]:
        """
        Calls a function from the provided function dictionary based on the function call details.

        Args:
            function_dict (Dict[str, Callable[..., Any]]): A dictionary mapping function names to their corresponding callables.
            function_call (types.FunctionCall): An object containing the name of the function to call and its arguments.

        Returns:
            Union[types.Part, List[types.Part]]: A single Part or a list of Parts containing the function response.

        Raises:
            Exception: If the function call results in an exception, it returns a Part with the exception details.
        """
        function_name = function_call.name
        function_args = function_call.args
        try:
            result = await self.function_dict[function_name](**function_args)
            if function_call.name == "get_website_screenshot":
                if not result["success"]:
                    types.Part().from_function_response(
                        name=function_call.name,
                        response={"result": "An error occurred taking the screenshot"},
                    ),
                else:
                    return [
                        types.Part().from_function_response(
                            name=function_call.name,
                            response={
                                "result": "Screenshot successfully taken and will be subsequently appended."
                            },
                        ),
                        get_image_part(
                            result.get(
                                "result",
                                f"function {function_call.name} encountered an error",
                            )
                        ),
                    ]
            else:
                return types.Part().from_function_response(
                    name=function_call.name,
                    response={
                        "result": result.get(
                            "result",
                            f"function {function_call.name} encountered an error",
                        ),
                    },
                )
        except Exception as exc:
            return types.Part().from_function_response(
                name=function_call.name,
                response={
                    "result": f"function {function_call.name} generated an exception: {exc}",
                },
            )

    async def generate_report(self, starting_parts):
        messages = [types.Content(parts=starting_parts, role="user")]
        completed = False
        think = True
        first_step = True
        try:
            while len(messages) < 50 and not completed:
                if first_step:
                    available_functions = ["infer_intent"]
                    think = False
                elif think and self.include_planning_step:
                    available_functions = ["plan_next_step"]
                else:
                    available_functions = [
                        definition["name"]
                        for definition in self.function_definitions
                        if definition["name"] not in ["plan_next_step", "infer_intent"]
                    ]
                tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=available_functions
                    )
                )
                response = self.client.models.generate_content(
                    model="gemini-2.0-flash-exp",
                    contents=messages,
                    config=types.GenerateContentConfig(
                        tools=[self.function_tool],
                        systemInstruction=self.system_prompt,
                        tool_config=tool_config,
                        temperature=0.1,
                    ),
                )
                function_call_promises = []
                messages.append(
                    types.Content(
                        parts=response.candidates[0].content.parts, role="model"
                    )
                )
                for part in response.candidates[0].content.parts:
                    if fn := part.function_call:
                        if fn.name == "submit_report_for_review":
                            return_dict = fn.args

                        function_call_promise = self.call_function(fn)
                        function_call_promises.append(function_call_promise)
                    else:
                        messages.append(
                            types.Content(
                                parts=[
                                    types.Part.from_text(
                                        "Error, not calling tools properly"
                                    )
                                ],
                                role="user",
                            )
                        )
                if len(function_call_promises) == 0:
                    think = not think
                    first_step = False
                    continue
                function_results = await asyncio.gather(*function_call_promises)
                response_parts = GeminiAgent.flatten_and_organise(function_results)
                # check if should end
                for part in response_parts:
                    if (
                        part.function_response is not None
                        and part.function_response.name == "submit_report_for_review"
                    ):
                        if part.function_response.response.get("result", {}).get(
                            "passedReview"
                        ):
                            return_dict["agent_trace"] = GeminiAgent.process_trace(
                                messages
                            )
                            return_dict["success"] = True
                            return return_dict
                messages.append(types.Content(parts=response_parts, role="user"))
                think = not think
                first_step = False
        except Exception as e:
            return {
                "error": str(e),
                "agent_trace": GeminiAgent.process_trace(messages),
                "success": False,
            }
        return {
            "success": False,
            "error": "Couldn't generate after 50 turns",
            "agent_trace": GeminiAgent.process_trace(messages),
        }

    async def generate_note(
        self,
        data_type: str = "text",
        text: Union[str, None] = None,
        image_url: Union[str, None] = None,
        caption: Union[str, None] = None,
    ):
        """Generates a community note based on the provided data type (text or image).

        Args:
            data_type: The type of data provided, either "text" or "image".
            text: The text content of the community note (required if data_type is "text").
            image_url: The URL of the image (required if data_type is "image").
            caption: An optional caption for the image.

        Returns:
            A dictionary representing the community note.
        """
        start_time = time.time()  # Start the timer
        summarise_report = summarise_report_factory(
            text, image_url, caption
        )  # Creates a function that takes in a report and summarises it.
        cost_tracker = {"total_cost": 0, "cost_trace": []}  # To store the cost details
        if "summarise_report" in self.function_dict:
            if self.function_dict["summarise_report"] is not None:
                print("Unexpected, summary function already inside function_dict")
            self.function_dict["summarise_report"] = summarise_report
        if data_type == "text":
            parts = generate_text_parts(text)

        elif data_type == "image":
            parts = generate_image_parts(image_url, caption)

        report_dict = await self.generate_report(parts.copy())

        duration = time.time() - start_time  # Calculate duration
        report_dict["agent_time_taken"] = duration
        time.sleep(3)
        if report_dict.get("success") and report_dict.get("report"):
            summary_results = await summarise_report(
                report_dict["report"]
            )  # Modify this line
            if summary_results.get("success"):
                report_dict["community_note"] = summary_results["community_note"]
                print("summary_generated")
            else:
                report_dict["community_note"] = None
            report_dict["total_time_taken"] = time.time() - start_time
            return report_dict
        else:
            return report_dict