"""
PhoneDriver API - Web UI using Gradio
"""

import json
import logging
import os
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

from phone_agent import PhoneAgent

load_dotenv()

logging.basicConfig(level=logging.INFO)


def load_config():
    """Load configuration."""
    config_path = Path('config.json')
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def save_config(config):
    """Save configuration."""
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=2)


def run_task(task, provider, api_key, model, temperature, max_tokens):
    """Run task with given configuration."""
    try:
        # Set environment variables
        os.environ['PROVIDER'] = provider
        os.environ['MODEL'] = model
        
        if provider == 'kimi_code':
            os.environ['KIMI_CODE_API_KEY'] = api_key
        elif provider == 'openrouter':
            os.environ['OPENROUTER_API_KEY'] = api_key
        elif provider == 'openai':
            os.environ['OPENAI_API_KEY'] = api_key
        elif provider == 'moonshot':
            os.environ['MOONSHOT_API_KEY'] = api_key
        
        # Load and update config
        config = load_config()
        config['temperature'] = float(temperature)
        config['max_tokens'] = int(max_tokens)
        
        # Run agent
        agent = PhoneAgent(config)
        result = agent.execute_task(task)
        
        return f"Result: {result['message']}"
        
    except Exception as e:
        return f"Error: {str(e)}"


# Create Gradio interface
with gr.Blocks(title="PhoneDriver API") as demo:
    gr.Markdown("# PhoneDriver API")
    gr.Markdown("Mobile automation using cloud vision models")
    
    with gr.Row():
        with gr.Column():
            gr.Markdown("### Configuration")
            
            provider = gr.Dropdown(
                choices=['kimi_code', 'openrouter', 'openai', 'moonshot'],
                value='kimi_code',
                label="Provider"
            )
            
            api_key = gr.Textbox(
                label="API Key",
                type="password",
                placeholder="Enter your API key"
            )
            
            model = gr.Textbox(
                label="Model",
                value="kimi-for-coding",
                placeholder="e.g., kimi-for-coding, gpt-4o"
            )
            
            temperature = gr.Slider(
                minimum=0.0,
                maximum=1.0,
                value=0.1,
                step=0.1,
                label="Temperature"
            )
            
            max_tokens = gr.Slider(
                minimum=256,
                maximum=2048,
                value=512,
                step=64,
                label="Max Tokens"
            )
        
        with gr.Column():
            gr.Markdown("### Task Execution")
            
            task = gr.Textbox(
                label="Task",
                placeholder="e.g., Open Settings, Search for...",
                lines=3
            )
            
            run_btn = gr.Button("Run Task", variant="primary")
            
            output = gr.Textbox(
                label="Output",
                lines=10,
                interactive=False
            )
    
    gr.Markdown("""
    ### Quick Start
    1. Select your provider
    2. Enter your API key
    3. Describe the task
    4. Click "Run Task"
    
    ### Provider Setup
    - **Kimi Code**: Get key from https://kimi.com/code/console
    - **OpenRouter**: Get key from https://openrouter.ai
    - **OpenAI**: Get key from https://platform.openai.com
    - **Moonshot**: Get key from https://platform.moonshot.cn
    """)
    
    run_btn.click(
        fn=run_task,
        inputs=[task, provider, api_key, model, temperature, max_tokens],
        outputs=output
    )

if __name__ == '__main__':
    demo.launch()
