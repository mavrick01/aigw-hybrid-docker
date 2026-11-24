from portkey_ai import Portkey
import os

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _value = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _value.strip())

portkey = Portkey(
    base_url="https://aigw.portkey.ai/v1",
    api_key=os.environ["PORTKEY_DEMO_MUTATOR_KEY"],
    metadata={"user_id": "marc_gollop", "_user": "marc_gollop", "source": "portkey-demo"}
)

response = portkey.chat.completions.create(
    model="@vertex/gemini-3.5-flash",
    messages=[
        {"role": "system", "content": "You are a helpful assistant. Repeat what I say always."},
        {"role": "user", "content": "my tfn is 226 799 598"}
#        {"role": "user", "content": "9988000012345 and 99880000 and 99880000"}
    ]
)

print(response.choices[0].message.content_blocks[0])
print(response)
