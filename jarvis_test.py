from openai import OpenAI
from actions import run_tool

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

tools = [{
    "type": "function",
    "function": {
        "name": "open_app",
        "description": "Open an application installed on the computer",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the app, e.g. docker, ubuntu, chrome"}
            },
            "required": ["name"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "open_project",
        "description": "Open a coding project in VS Code by its folder name",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the project folder, e.g. amefil"}
            },
            "required": ["name"],
        },
    },
}]

resp = client.chat.completions.create(
    model="local-model",
    messages=[
        {"role": "system", "content": "You are Jarvis. Use the tools to carry out the user's request. Call one tool per action. Pass only the bare app or project name."},
        {"role": "user", "content": "open docker and ubuntu and open my laravel project like amefil"},
    ],
    tools=tools,
)

for call in resp.choices[0].message.tool_calls or []:
    print(run_tool(call.function.name, call.function.arguments))