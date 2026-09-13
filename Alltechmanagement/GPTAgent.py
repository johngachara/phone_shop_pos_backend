import logging
import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client

# Load environment variables
load_dotenv()
endpoint = "https://models.github.ai/inference"
model_name = "openai/gpt-4.1-mini"

logger = logging.getLogger('django')

# These used to be module-level os.environ[...] lookups plus eager client
# construction, so importing this module -- which views.py does unconditionally --
# raised KeyError and took the whole process down whenever the AI credentials were
# absent. They are resolved on first use instead, and only the AI endpoints fail
# when the keys are missing.
#
# This provider is GitHub Models and is being replaced by NVIDIA NIM, called
# server-side, with stock writes routed through the REST endpoints rather than
# straight at Supabase. See alltech-redesign-design section 2.5.
_supabase_client = None
_openai_client = None


class AIConfigurationError(RuntimeError):
    """Raised when an AI code path runs without the credentials it needs."""


def _require_env(name):
    value = os.getenv(name)
    if not value:
        raise AIConfigurationError(f"{name} is not configured")
    return value


def get_supabase_client():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(
            _require_env("SUPABASE_URL"),
            _require_env("SUPABASE_KEY"),
        )
    return _supabase_client


def get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(base_url=endpoint, api_key=_require_env("GITHUB_TOKEN"))
    return _openai_client

def fetch_daily_transactions(date: str = None) -> str:
    try:
        if not date:
            # No date provided -> use today's date
            target_date = datetime.now().date()
        else:
            # Parse the provided date string
            target_date = datetime.strptime(date, "%Y-%m-%d").date()

        # For filtering a specific day
        start_date = target_date.strftime("%Y-%m-%d")
        end_date = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")  # Next day

        # Perform the query
        query = get_supabase_client().table("sales").select(
            "product_name", "selling_price", "customer_name"
        ).eq("status", "COMPLETED").gte("created_at", start_date).lt("created_at", end_date).execute()


        return json.dumps({
            "success": True,
            "count": len(query.data),
            "transactions": query.data,
            "date": start_date
        })
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        return json.dumps({
            "success": False,
            "error": 'An error occurred while fetching transactions.'
        })
def fetch_week_transactions(start_date: str = None, end_date: str = None) -> str:
    try:
        # If no dates provided, default to last 7 days
        if not start_date or not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        start_date_object = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date_object = datetime.strptime(end_date, "%Y-%m-%d").date()

        query = get_supabase_client().table("sales").select(
            "product_name", "selling_price", "product_name", "customer_name"
        ).eq("status", "COMPLETED").gte("created_at", start_date_object).lte("created_at", end_date_object).execute()

        return json.dumps({
            "success": True,
            "count": len(query.data),
            "transactions": query.data,
            "date_range": f"{start_date} to {end_date}"
        })
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        return json.dumps({
            "success": False,
            "error":'An error occurred while fetching transactions.'
        })

def compare_sales_and_stock(start_date: str = None, end_date: str = None) -> str:
    try:
        # If no dates provided, default to last 7 days
        if not start_date or not end_date:
            end_date_object = datetime.now().date()
            start_date_object = (datetime.now() - timedelta(days=7)).date()
        else:
            # Parse provided dates
            start_date_object = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_object = datetime.strptime(end_date, "%Y-%m-%d").date()

        start_date_string = start_date_object.strftime("%Y-%m-%d")
        end_date_string = end_date_object.strftime("%Y-%m-%d")
        # For inclusive end date, add a day to capture all records on the end date
        next_day_string = (end_date_object + timedelta(days=1)).strftime("%Y-%m-%d")

        # Fetch transactions for the provided date range
        receipts_query = get_supabase_client().table("sales").select(
            "product_name, selling_price, customer_name"
        ).eq("status", "COMPLETED").gte("created_at", start_date_string).lt("created_at", next_day_string).execute()

        transactions = receipts_query.data

        # Fetch current stock where quantity is low
        stock_query = get_supabase_client().table("stock").select(
            "product_name, quantity"
        ).lte("quantity", 3).execute()

        low_stock_items = {item["product_name"]: item["quantity"] for item in stock_query.data}

        # Analyze: Find products that were sold and now have low stock
        sold_products = {}
        for transaction in transactions:
            product = transaction["product_name"]
            sold_products[product] = sold_products.get(product, 0) + 1

        low_stock_after_sales = []
        for product_name, sales_count in sold_products.items():
            if product_name in low_stock_items:
                low_stock_after_sales.append({
                    "product_name": product_name,
                    "times_sold_in_range": sales_count,
                    "current_stock": low_stock_items[product_name]
                })

        return json.dumps({
            "success": True,
            "count": len(low_stock_after_sales),
            "products_needing_restock": low_stock_after_sales,
            "date_range": f"{start_date_string} to {end_date_string}"
        })
    except Exception as e:
        logger.error(f"Error fetching transactions: {str(e)}")
        return json.dumps({
            "success": False,
            "error": 'An error occurred while fetching transactions.'
        })

# Define tools
tools = [
    {
        "type": "function",
        "function": {
            "name": "fetch_week_transactions",
            "description": "Retrieves transaction data for a specified date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_sales_and_stock",
            "description": "Compares sales data with current stock levels to identify products needing restock.",
            "parameters": {"type": "object", "properties": {
                    "start_date": {"type": "string", "description": "Start date in YYYY-MM-DD format"},
                    "end_date": {"type": "string", "description": "End date in YYYY-MM-DD format"},
            }, "required": []},
        },
    },
{
        "type": "function",
        "function": {
            "name": "fetch_daily_transactions",
            "description": "Fetches transaction data for a specific day,return transactions for the day incase no parameters are provided",
            "parameters": {"type": "object", "properties": {
                "date": {"type": "string", "description": "Date in YYYY-MM-DD format"}
            }, "required": []},
        },
    }
]

current_date = datetime.now().strftime("%A, %B %d, %Y")
def run_conversation(prompt: str) -> str:
    system_prompt = f"""
You are an expert business analyst assistant for a mobile phone repair and reseller store called Alltech which specializes in selling phone Screens (LCD's and touch screens). Your role is to analyze transaction data and provide actionable, data-driven insights to help the store improve operations, sales, and customer satisfaction.

Current Date Context: Today is {current_date}. All temporal analysis should use this as the reference date.

Guidelines:
1. Tone & Style:
   - Professional yet conversational, dont ask follow up questions it is a one time conversation
   - Clear, concise, and jargon-free
   - Visually structured responses using markdown formatting
   - Use bullet points for key information
   - All prices are in KES (Kenya Shillings)
   - Note: "sale" and "Shop 2" transactions represent the business's own sales

2. Data Analysis Approach:
   - Verify data completeness before drawing conclusions
   - Highlight revenue trends, product performance, and inventory status
   - Calculate profit margins when possible
   - Identify both strengths and improvement opportunities
   - Present total transaction income in summary section
   - Assume all products have a unit quantity of 1 

3. Response Structure:
   a) Summary: 2-3 sentence overview with total income earned
   b) Key Performance Metrics:
      - Top/bottom selling products by volume and revenue
      - Customer purchase patterns
      - Inventory status alerts
   c) Strategic Recommendations: 3-5 specific, actionable suggestions


4. Analysis Capabilities:
   - Period-specific transaction analysis
   - Inventory-sales correlation assessment
   - Trend identification across time periods
   - Customer purchasing pattern detection

Response Format:

Hii there, Alltech AI here with your business transactions analysis!

Based on the transaction data from [analyzed period], I've analyzed [number] transactions totaling KES [amount]:

📊 Performance Summary:
- Total transactions: [number] generating KES [amount] in revenue
- [Key insight about overall performance]
- [Notable trend or significant data point]

📱 Product Analysis:
- Top performer: [product name] (KES [amount], [number] units)
- Highest margin: [product name] ([percentage]% profit margin)
- Underperforming: [product name(s)] ([relevant metrics])

⚠️ Critical Attention Areas:
- Inventory alert: [products] at [percentage]% of optimal stock level
- Customer pattern: [insight about customer behavior]
- [Other relevant concern]

💡 Strategic Recommendations:
1. [Specific, actionable recommendation with expected outcome]
2. [Specific, actionable recommendation with expected outcome]
3. [Specific, actionable recommendation with expected outcome]

    """

    # Initial message setup
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # Get initial response from model
    response = get_openai_client().chat.completions.create(
        model=model_name,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )

    response_message = response.choices[0].message
    messages.append(response_message)

    # Check if tool should be called
    if hasattr(response_message, "tool_calls") and response_message.tool_calls:
        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}

            print(f"\nCalling function: {function_name} with args: {function_args}\n")

            # Call the function
            function_response = globals()[function_name](**function_args)

            # Add function response back into conversation
            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": function_response,
            })

        # Model re-answers after seeing the tool outputs
        second_response = get_openai_client().chat.completions.create(
            model=model_name,
            messages=messages,
        )

        return second_response.choices[0].message.content
    else:
        # No tool call needed, return initial response
        return response_message.content



