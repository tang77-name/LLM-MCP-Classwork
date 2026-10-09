import configparser
from openai import OpenAI

config = configparser.ConfigParser()
config.read("config.ini", encoding="utf-8")

client = OpenAI(
    base_url=config.get("llm", "base_url"),
    api_key=config.get("llm", "api_key"),
)

prompt = input("请输入提示词：")

stream = client.chat.completions.create(
    model=config.get("llm", "model_name"),
    messages=[{"role": "user", "content": prompt}],
    stream=True,
)

for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
print()
