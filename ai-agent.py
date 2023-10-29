#!/usr/bin/env python3
import os
from random import randint
from re import T
import time
from xml.dom.xmlbuilder import Options
import pytest
import time
from playwright.sync_api import sync_playwright
import time
from sys import argv, exit, platform
import openai
import os
from anthropic import Anthropic, HUMAN_PROMPT, AI_PROMPT
import chatgpt
from string import Formatter
from typing import Union, Type
from pydantic import BaseModel
from pydantic import create_model
from natbot import *


class Reasoner:
    def __init__(self, system_prompt=None, model="gpt-4"):
        self.model = model
        self.messages = []
        if system_prompt:
            self.messages.append({"role": "system", "content": system_prompt})
        self._is_internal = False

    def add_message(self, role, message, name=None):
        msg = {"role": role, "content": message}
        if name:
            msg["name"] = name
        self.messages.append(msg)

    def set_message(self, role, message, name=None):
        self.messages = []
        msg = {"role": role, "content": message}
        if name:
            msg["name"] = name
        self.messages.append(msg)

    def external_dialogue(self, thought):
        # thought should describe how to respond, e.g. "I should respond to the user with the joke I came up with."
        self.add_message("assistant", "[Internal Monologue]: " + thought)
        if self._is_internal:
            self._is_internal = False
            self.add_message(
                "assistant",
                "[Internal Monologue]: I am now entering the external dialogue state. Everything I say there will be seen.",
            )
            self.add_message(
                "function", "[Exited Internal Monologue]", "exit_monologue"
            )
        response = chatgpt.complete(
            messages=self.messages, model=self.model, use_cache=False
        )
        self.add_message("assistant", response)
        return response

    def internal_monologue(self, thought):
        if not self._is_internal:
            self._is_internal = True
            self.add_message(
                "function", "[Entered Internal Monologue]", "enter_monologue"
            )
            self.add_message(
                "assistant",
                "[Internal Monologue]: I am now in the internal monologue state. I won't be able to respond here, so I'll use this space to think, reflect, and plan.",
            )
        self.add_message("assistant", "[Internal Monologue]: " + thought)
        response = chatgpt.complete(
            messages=self.messages, model=self.model, use_cache=False
        )
        response = response.replace("[Internal Monologue]: ", "")
        self.add_message("assistant", "[Internal Monologue]: " + response)
        return response


class StructuredReasoner(Reasoner):
    def __init__(self, system_prompt=None, model="gpt-4"):
        super().__init__(system_prompt=system_prompt, model=model)

    def parse_response_options(self):
        json_schema = {
            "name": "store_response_options",
            "description": "Stores a list of possible response options in memory to choose from later. E.g. ['attempt to explain mathematically', 'explain using an analogy', 'list resources to learn more']",
            "parameters": {
                "type": "object",
                "properties": {
                    "responses": {
                        "description": "The list of possible response options. Each element should be a short summary, not a full response.",
                        "type": "array",
                        "items": {"type": "string"},
                    }
                },
                "required": ["responses"],
            },
        }
        response = chatgpt.complete(
            messages=self.messages,
            model=self.model,
            functions=[json_schema],
            function_call={"name": "store_response_options"},
            use_cache=False,
        )
        if response["role"] != "function":
            raise Exception(f"Expected a function call, but got: {response['content']}")
        repsonse_options = response["args"]["responses"]
        self.add_message(
            response["role"],
            "Stored response options:" + "\n".join(repsonse_options),
            name=response["name"],
        )
        return repsonse_options

    def choose(self, options):
        self.add_message(
            "assistant",
            "[Internal Monologue]: I need to record my choice as one of the following, "
            "by calling the choose() function with the corresponding choice number:\n"
            + "\n".join([f"{i+1}. {option}" for i, option in enumerate(options)]),
        )
        json_schema = {
            "name": "choose",
            "description": "Chooses one of the options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "choice_index": {
                        "description": f"The index of the option you chose. An integer from 1 to {len(options)}",
                        "type": "integer",
                    }
                },
                "required": ["options"],
            },
        }
        response = chatgpt.complete(
            messages=self.messages,
            model=self.model,
            functions=[json_schema],
            function_call={"name": "choose"},
            use_cache=False,
        )
        if response["role"] != "function":
            raise Exception(f"Expected a function call, but got: {response['content']}")
        self.messages.pop()  # remove the message that prompted the user to choose
        choice = response["args"]["choice_index"] - 1
        self.add_message(
            response["role"], f"Chose option: {options}", name=response["name"]
        )
        return choice


class FancyStructuredReasoner(Reasoner):
    def __init__(self, system_prompt=None, model="gpt-4"):
        super().__init__(system_prompt, model)

    def extract_info(self, info_format, output_type: Union[BaseModel, Type]):
        """
        Extracts a piece of information in a specific format.
        This is done by using the function calling API to create a remember_{field_name} function and executing it.

        This function is useful when you want to extract the outcome of an internal monologue in a specific format.
        It doesn't work so well for reasoning, so stick to the paradigm of internal monologue -> extract_info.
        The format string is a python format string that determines the format of the stored information.

        Parameters:
        info_format (str):
            The format string that determines the format of the stored information.
        output_type (Union[BaseModel, Type]):
            The type of the field to be extracted.
            If a pydantic BaseModel is provided, the field is extracted as a pydantic model.
            If a python Type is provided, the field is extracted as an instance of that type.

        Returns:
        The value of the field remembered by the reasoner

        Examples:
        --------
        Extracting an integer:
        >>> reasoner.add_message('user', "My name's Bill, I'm a 42 y.o. male from New York.")
        >>> reasoner.extract_info("The user is {age} years old.", int)
        25

        Extracting an enum:
        >>> from enum import Enum
        >>> reasoner.add_message("assistant", "I have logically deduced that I am happy.")
        >>> reasoner.extract_info("I am {state}", Enum('MentalState', 'HAPPY SAD'))
        "HAPPY"

        Extracting a pydantic model:
        >>> from pydantic import BaseModel
        >>> class Person(BaseModel):
        ...     name: str
        ...     twitter_handle: str
        ...     is_based: bool = False
        >>> reasoner.add_message("user", "Add Ivan Yevenko (@ivan_yevenko) to the database, he's pretty based.")
        >>> reasoner.extract_info("Added {person} to the database.", Person)
        Person(name='Ivan Yevenko', twitter_handle='@ivan_yevenko', is_based=True)
        """
        formatter = Formatter()
        parsed = [x for x in formatter.parse(info_format) if x[1] is not None]
        assert len(parsed) == 1, "Only one format field is allowed."

        _, field_name, _, _ = parsed[0]

        use_pydantic = type(output_type) is type and issubclass(output_type, BaseModel)
        if use_pydantic:
            params = output_type.model_json_schema()
        else:
            SingleFieldModel = create_model(
                "SingleFieldModel", **{field_name: (output_type, ...)}
            )
            params = SingleFieldModel.model_json_schema()

        func_name = "remember_" + field_name
        json_schema = {
            "name": func_name,
            "description": f"This function stores a piece of information in the format: '{info_format}'.",
            "parameters": params,
        }

        response = chatgpt.complete(
            messages=self.messages,
            model=self.model,
            functions=[json_schema],
            function_call={"name": func_name},
            use_cache=False,
        )
        if response["role"] != "function":
            raise Exception(f"Expected a function call, but got: {response['content']}")

        value = response["args"]
        if use_pydantic:
            value = output_type.model_construct(value)
        else:
            try:
                value = value[field_name]
            except KeyError:
                # Generated JSON schema is sometimes incorrect, so we try to extract the field anyway
                value = value.popitem()[1]

        info = info_format.format(**{field_name: value})
        self.add_message(
            "function", f'Stored information: "{info}"', name=response["name"]
        )
        return value


def breakintosubcommands(command):
    system_prompt = (
        "You use your internal monologue to reason before responding to the user. "
        "You only return a python array of information in your responses"
        "You are responsible for breaking a command into substeps which will be executed by a web crawler"
        "You are responsible for visiting the MIT Opencourse website, given a course_code and assignment, you are supposed to navigate to that, download the particular pdf for that assignment"
        "Return specific steps which can be executed another tool which crawls the web"
    )
    reasoner = StructuredReasoner(system_prompt=system_prompt, model="gpt-3.5-turbo")

    THINK_FIRST = True
    reasoner.add_message("user", command)
    options = reasoner.parse_response_options()
    print(options)
    return options


# Find assignment number and MIT Courseware code
def extract_ass_num_course_num(command):
    system_prompt = (
        "You use your internal monologue to reason before responding to the user. "
        "You try to extract important fields from a prompt"
    )

    reasoner = FancyStructuredReasoner(
        system_prompt=system_prompt, model="gpt-3.5-turbo"
    )
    reasoner.add_message("user", command)
    assignment = reasoner.extract_info("The assingment number is {num}", int)
    course_code = reasoner.extract_info("The course code is {num}", float)

    return assignment, course_code


def debator1_func():
    system_prompt = (
        "You use your internal monologue to reason before responding to the user. "
        "You are a participant in an ai debate competition. "
        "Give clear and strong arugments responsing to each question"
        "You will start the debate"
        "Keep your responses short"
        "Don't repeat your previous answers"
    )
    reasoner = StructuredReasoner(system_prompt=system_prompt, model="gpt-4")
    return reasoner


def debator2_func():
    system_prompt = (
        "You use your internal monologue to reason before responding to the user. "
        "You are a participant in an ai debate competition. "
        "You will be answering second in the debate after the first person"
        "Give clear and strong arugments responsing to each question"
        "Keep your responses short"
        "Don't repeat your previous answers"
    )
    reasoner = StructuredReasoner(system_prompt=system_prompt, model="gpt-4")
    return reasoner


def summary_func():
    system_prompt = "You will summarize for the debate points."
    reasoner = StructuredReasoner(system_prompt=system_prompt, model="gpt-3.5-turbo")
    return reasoner


def ai_debate(topic):
    THINK_FIRST = True
    debator1 = debator1_func()
    debator2 = debator2_func()
    summer = summary_func()

    debate_topic = "The debate topic is: " + topic
    # debator1.add_message("user", debate_topic)
    # debator2.add_message("user", debate_topic)
    count = 0

    message1 = ""
    history1 = []
    history2 = []
    while True:
        # if count == 0:
        # if count == 0:
        #     debator1.add_message(
        #         "user", "[Internal Monologue]: Topic of the debate is: " + message1
        #     )
        # else:
        #     debator1.add_message(
        #         "user", "[Internal Monologue]: another debater's answer is" + message1
        #     )
        # else:
        #     debator1.add_message("")
        history1_str = "\n".join(history1)
        history2_str = "\n".join(history2)
        if len(history1) == 0:
            debator1.set_message(
                "user",
                f"[Internal Monologue]: Topic of the debate is: {topic}.",
            )
        else:
            debator1.set_message(
                "user",
                f"[Internal Monologue]: Topic of the debate is: {topic}. My preivous arguments are {history1_str}. My opponent previous arguments are {history2_str}. Please don't repeat yourself and say one argument per time",
            )

        if THINK_FIRST:
            thought = debator1.internal_monologue(
                "I should brainstorm one different argument to agree on this topic."
            )
            # print(f"Though for Debator 1: {thought}")
        else:
            debator1.add_message(
                "assistant",
                "[Internal Monologue]: I should speak in favour of this viewpoint and respond to the other debator.",
            )
        options = debator1.parse_response_options()

        if THINK_FIRST:
            thought = debator1.internal_monologue(
                "I need to choose the cleverest response, I can only choose one which can against with my opponent or agree on this topic. My argument is:\n"
                + "\n".join(options)
            )
            # print(f"Though for Debator 1: {thought}")
        else:
            debator1.add_message(
                "assistant", "[Internal Monologue]: I need to choose the best response"
            )
        choice = debator1.choose(options)

        response = debator1.external_dialogue(
            f"I'll respond to the user using the response I chose and also present a single argument is less than 100 words."
        )
        history1.append(response)
        print("\nAI Agent 1: " + response)
        time.sleep(0.5)
        debator1.add_message(
            "user", "[Internal Monologue] my last answer is: " + response
        )

        history1_str = "\n".join(history1)
        history2_str = "\n".join(history2)
        debator2.set_message(
            "user",
            f"[Internal Monologue]: Topic of the debate is: {topic}. My preivous arguments are {history1_str}. My opponent previous arguments are {history2_str}. Please don't repeat yourself and say one argument per time",
        )

        # debator2.add_message(
        #     "user", "[Internal Monologue] another debater's answer is:" + response
        # )
        if THINK_FIRST:
            thought = debator2.internal_monologue(
                "I should strongly disagree with the viewpoint and respond to my opponent"
            )
            # print(f"Though for Debator 2: {thought}")
        else:
            debator2.add_message(
                "assistant",
                "[Internal Monologue]: I should brainstorm a list of arguments to against with the viewpoint and response to my opponent.",
            )
        options = debator2.parse_response_options()

        if THINK_FIRST:
            thought = debator2.internal_monologue(
                "I need to choose the strongest response, I can only choose one. My argument is:\n"
                + "\n".join(options)
            )
            # print(f"Though for Debator 2: {thought}")
        else:
            debator2.add_message(
                "assistant",
                "[Internal Monologue]: I need to choose the best response disagree with my opponent",
            )
        choice = debator2.choose(options)

        response = debator2.external_dialogue(
            f"II'll respond to the user using the response I chose and also present a single argument is less than 100 words."
        )
        history2.append(response)
        print("\nAI Agent 2: " + response)
        time.sleep(0.5)
        debator2.add_message("user", "your last response was: " + response)
        message1 = response

        # compression
        history1 = summary(summer, history1)
        history2 = summary(summer, history2)

        count = count + 1
        if count == 4:
            break
    print("\nConclusions: ")
    response1 = debator1.external_dialogue(
        f"I will summarize what are objective facts vs subjective opionions for this topic"
    )
    print("\nAI Agent 1: " + response1)
    response2 = debator2.external_dialogue(
        f"I will summarize what are objective facts vs subjective opionions for this topic, regarding with my opponent said {response1}"
    )
    print("\nAI Agent 2: " + response2)


def summary(summarizer, history):
    if len(history) > 5:
        summarier.set_message("\n".join(history))
        response = summarier.external_dialogue(f"I'll summary it within 200 words")
        return [response]
    return history


if __name__ == "__main__":
    topic = input("Choose topic: ")
    ai_debate(topic)
