import yaml

from llm.provider import get_provider

with open('config.yaml') as f:
    config = yaml.safe_load(f)

task = 'synsthesis'
test_llm = get_provider(task, config)
response = test_llm.chat("What is going on?")
print(response)


# import yaml
# from llm.provider import get_provider

# with open("config.yaml") as f:
#     config = yaml.safe_load(f)

# test_llm = get_provider("synthesis", config=config)
# print(response)
