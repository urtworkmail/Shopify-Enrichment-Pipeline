import anthropic, os
from dotenv import load_dotenv
load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
try:
    msg = client.messages.create(
        model='claude-sonnet-4-5-20250929',
        max_tokens=100,
        messages=[{'role':'user','content':'Return a JSON object with a single key test and value hello'}]
    )
    print('Status: success')
    print('Raw response:', repr(msg.content[0].text[:300]))
    print('Input tokens:', msg.usage.input_tokens)
    print('Output tokens:', msg.usage.output_tokens)
except Exception as e:
    print('Error:', repr(e))
