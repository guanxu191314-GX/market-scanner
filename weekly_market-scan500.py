import yfinance as yf
import pandas as pd
import requests
import smtplib
import os
from io import StringIO
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# --- Email Configuration ---
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD") # Must be an App Password
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL")

def get_sp500_tickers():
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status() 
        
        # Wrapped in StringIO to prevent Pandas future warnings
        tables = pd.read_html(StringIO(response.text), match='Symbol')
        tickers = tables[0]['Symbol'].tolist()
        
        # Clean tickers for yfinance (e.g., BRK.B -> BRK-B)
        tickers = [str(ticker).replace('.', '-') for ticker in tickers]
        return tickers
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Wikipedia page: {e}")
        return [] 

def find_3_candle_reversals(df):
    signals = []
    last_signal = 0 
    if len(df) < 3:
        return signals

    for i in range(2, len(df)):
        O2, H2, L2, C2 = df['Open'].iloc[i-2], df['High'].iloc[i-2], df['Low'].iloc[i-2], df['Close'].iloc[i-2]
        O1, H1, L1, C1 = df['Open'].iloc[i-1], df['High'].iloc[i-1], df['Low'].iloc[i-1], df['Close'].iloc[i-1]
        O0, H0, L0, C0 = df['Open'].iloc[i], df['High'].iloc[i], df['Low'].iloc[i], df['Close'].iloc[i]
        date_current = df.index[i]

        raw_bottom = (L1 < L2) and (L1 < L0) and (C0 > H2) and (C0 > O0)
        raw_top = (H1 > H2) and (H1 > H0) and (C0 < L2) and (C0 < O0)

        if raw_bottom and last_signal != 1:
            signals.append({'Date': date_current, 'Signal': 'Bottom (Bullish)'})
            last_signal = 1
        elif raw_top and last_signal != -1:
            signals.append({'Date': date_current, 'Signal': 'Top (Bearish)'})
            last_signal = -1
    return signals

def send_email(df_results):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAIL:
        print("Email credentials missing. Skipping email notification.")
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = "Weekly S&P 500 Reversal Pattern Scan"

    if df_results.empty:
        html_content = "<h3>No matching setups found this week.</h3>"
    else:
        # Create a copy so we don't format the raw data with HTML tags in the console
        df_email = df_results.copy()
        
        # Simplified HTML Anchor Tag to prevent Email clients from stripping it out
        df_email['Ticker'] = df_email['Ticker'].apply(
            lambda t: f'<a href="https://www.tradingview.com/symbols/{str(t).strip()}/">{str(t).strip()}</a>'
        )

        # MUST USE escape=False to render the HTML anchor tags properly
        html_table = df_email.to_html(index=False, border=1, justify="center", escape=False)
        
        # Inject CSS Colors into the HTML string for the email
        html_table = html_table.replace('Continuation (Bullish)', '<span style="color: green; font-weight: bold;">Continuation (Bullish)</span>')
        html_table = html_table.replace('Bottom (Reversal)', '<span style="color: green; font-weight: bold;">Bottom (Reversal)</span>')
        html_table = html_table.replace('Continuation (Bearish)', '<span style="color: red; font-weight: bold;">Continuation (Bearish)</span>')
        html_table = html_table.replace('Top (Reversal)', '<span style="color: red; font-weight: bold;">Top (Reversal)</span>')

        html_content = f"""
        <html>
          <head></head>
          <body>
            <h3>S&P 500 - 3-Candle Pivot Reversals (Weekly)</h3>
            <p><strong>Criteria:</strong> Weekly charts. Filtered by 5-Week SMA momentum. Context applied via 60-Week SMA.</p>
            {html_table}
          </body>
        </html>
        """

    msg.attach(MIMEText(html_content, 'html'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Email sent successfully with clickable links!")
    except Exception as e:
        print(f"Failed to send email: {e}")

# --- Main Execution ---
print("Fetching S&P 500 tickers...")
tickers = get_sp500_tickers()
if not tickers:
    print("No tickers found. Exiting.")
    exit()

cutoff_date = pd.Timestamp.now().normalize() - pd.Timedelta(days=56) # 8 Weeks lookback cutoff
results = []

# period="5y" (to ensure we have 60 weeks of data) and interval="1wk" for weekly candles
data = yf.download(tickers, period="5y", interval="1wk", group_by="ticker", auto_adjust=False, threads=True)

for ticker in tickers:
    try:
        if ticker in data.columns.levels[0]:
            df = data[ticker].copy().dropna()
        else:
            continue
            
        # Require 60 weeks of data to calculate the MA60
        if len(df) < 60:
            continue
            
        df['SMA_5'] = df['Close'].rolling(window=5).mean()
        df['SMA_60'] = df['Close'].rolling(window=60).mean()
        last_close = df['Close'].iloc[-1]
        last_sma5 = df['SMA_5'].iloc[-1]
        last_sma60 = df['SMA_60'].iloc[-1]

        stock_signals = find_3_candle_reversals(df)
        if stock_signals:
            last_sig = stock_signals[-1] 
            
            if last_sig['Date'] >= cutoff_date:
                is_bullish = 'Bottom' in last_sig['Signal']
                
                # Directional 5 SMA filter
                if is_bullish and last_close <= last_sma5:
                    continue 
                if not is_bullish and last_close >= last_sma5:
                    continue 

                # Renaming logic based on MA 60
                if is_bullish:
                    if last_close > last_sma60:
                        final_signal = 'Continuation (Bullish)'
                    else:
                        final_signal = 'Bottom (Reversal)'
                else:
                    if last_close < last_sma60:
                        final_signal = 'Continuation (Bearish)'
                    else:
                        final_signal = 'Top (Reversal)'

                results.append({
                    'Ticker': ticker,
                    'Date': last_sig['Date'].strftime('%Y-%m-%d'),
                    'Signal': final_signal,
                    'Last Close': round(last_close, 2),
                    '5-Week SMA': round(last_sma5, 2),
                    '60-Week SMA': round(last_sma60, 2)
                })
    except Exception:
        continue

results_df = pd.DataFrame(results)
if not results_df.empty:
    results_df = results_df.sort_values(by=['Date', 'Ticker'], ascending=[False, True]).reset_index(drop=True)

# Print to console (for Action logs) and send email
print(results_df)
send_email(results_df)
