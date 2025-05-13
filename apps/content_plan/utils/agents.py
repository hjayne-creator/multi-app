import os
import json
import logging
from flask import current_app
from .openai_client import get_openai_client
import time
import tiktoken

logger = logging.getLogger(__name__)

def count_tokens(text, model="gpt-4o-mini"):
    """Count the number of tokens in a text string."""
    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception as e:
            logger.warning(f"Could not get tokenizer for {model}: {str(e)}. Using cl100k_base instead.")
            encoding = tiktoken.get_encoding("cl100k_base")  # Fallback to cl100k_base (used by gpt-4 and other recent models)
        return len(encoding.encode(text))
    except Exception as e:
        logger.error(f"Error counting tokens: {str(e)}")
        # Fallback to rough estimation (1 token ≈ 4 characters)
        return len(text) // 4

def truncate_text(text, max_tokens, model="gpt-4o-mini"):
    """Truncate text to fit within token limit."""
    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception as e:
            logger.warning(f"Could not get tokenizer for {model}: {str(e)}. Using cl100k_base instead.")
            encoding = tiktoken.get_encoding("cl100k_base")  # Fallback to cl100k_base (used by gpt-4 and other recent models)
        tokens = encoding.encode(text)
        if len(tokens) > max_tokens:
            truncated_tokens = tokens[:max_tokens]
            return encoding.decode(truncated_tokens) + "... (truncated)"
        return text
    except Exception as e:
        logger.error(f"Error truncating text: {str(e)}")
        # Fallback to character-based truncation
        return text[:max_tokens * 4] + "... (truncated)"

def run_agent_with_openai(system_message, user_message, model=None):
    """
    Run a prompt using the OpenAI chat completions API with enhanced error handling and logging.
    """
    try:
        # Get the model from config if not provided
        model = model or current_app.config.get('OPENAI_MODEL', current_app.config.get('OPENAI_MODEL_FALLBACK', 'gpt-4o-mini'))
        client = get_openai_client()

        # Calculate token counts
        system_tokens = count_tokens(system_message, model)
        user_tokens = count_tokens(user_message, model)
        total_input_tokens = system_tokens + user_tokens

        # Set max tokens for completion (leave room for input)
        max_completion_tokens = 4000  # Reduced from 4000
        max_input_tokens = 4000  # Leave room for completion
        
        logger.info(f"Token counts - System: {system_tokens}, User: {user_tokens}, Total: {total_input_tokens}")

        # Truncate messages if needed
        if total_input_tokens > max_input_tokens:
            logger.warning(f"Input exceeds token limit ({total_input_tokens} > {max_input_tokens}), truncating...")
            # Truncate user message (usually the longer one)
            user_message = truncate_text(user_message, max_input_tokens - system_tokens, model)
            logger.info("User message truncated")

        # Add timeout and retry logic
        max_retries = 2
        retry_delay = 6  # seconds
        last_error = None

        for attempt in range(max_retries):
            try:
                start_time = time.time()
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message}
                    ],
                    temperature=0.7,
                    max_tokens=max_completion_tokens,
                    timeout=60  # 60 second timeout
                )
                end_time = time.time()
                logger.info(f"OpenAI API call completed in {end_time - start_time:.2f} seconds")

                if response.choices:
                    return response.choices[0].message.content.strip()

                raise Exception("OpenAI returned an empty response.")

            except Exception as e:
                last_error = e
                logger.error(f"OpenAI API call attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    raise

    except Exception as e:
        error_msg = f"OpenAI API call failed after {max_retries} attempts: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise Exception(error_msg)  # Re-raise with more context

