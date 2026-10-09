import configparser
from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

client = OpenAI(
    base_url=config.get("llm", "base_url"),
    api_key=config.get("llm", "api_key"),
)

messages = []

while True:
    prompt = input("请输入提示词：")
    messages.append({"role": "user", "content": prompt})

    stream = client.chat.completions.create(
        model=config.get("llm", "model_name"),
        messages=messages,
        stream=True,
    )

    answer = ""
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            answer += delta
            print(delta, end="", flush=True)
    print()

    messages.append({"role": "assistant", "content": answer})
