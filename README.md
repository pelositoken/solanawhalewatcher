# 🧙‍♂️ Solana Wizard Bot 🚀  
A **feature-packed** Telegram bot for Solana traders.  
Automate transactions, track new pairs, manage wallets, and snipe tokens with ease!  

---

## 🌟 Features  
✔️ **Solana Wallet Integration** – Create and manage your wallet.  
✔️ **Real-time SOL Balance & USD Conversion** – Track holdings with accurate pricing.  
✔️ **Automated Trading** – Buy & sell tokens directly from Telegram.  
✔️ **Whale Watching & Token Sniping** – Be first to new token listings.  
✔️ **Transaction History** – View past trades.  
✔️ **Multi-Token Support** – Track and trade all Solana-based tokens.  
✔️ **Inline Keyboards & Quick Actions** – User-friendly interface.  

---

## 🔧 Installation  

### 1️⃣ Clone the Repository  
```sh
git clone https://github.com/rgodlontonshaw/solanawizard_bot.git
cd solanawizard_bot/src
```

### 2️⃣ Install Dependencies  
```sh
npm install
```

### 3️⃣ Set Up Environment Variables  
Create a `.env` file and add the following:  
```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
RPC_URL=https://solana-mainnet-rpc-url
```

### 4️⃣ Start the Bot  
```sh
node index.js
```

---

## 🎮 Commands & Functions  

| Command | Function |
|---------|----------|
| `/start` | Create a new wallet & show balance |
| `/profile` | View wallet details & SOL balance |
| `/quicktrade` | Fast buy & sell interface |
| `/trades_history` | View past trades |
| `/newpairs` | Get alerted on new Solana token pairs |
| `/settings` | Configure bot settings |
| `/help` | List all commands |

---

## 💰 Trading Features  

### 🏦 **Wallet Management**  
- Automatically creates a **Solana Wallet** for each user.  
- Securely **stores and retrieves** wallet details.  
- Displays **SOL balance** with **USD conversion**.  
- Shows **token holdings** & market values.  

### 💹 **Trading & Sniping**  
- **Buy tokens** by pasting a **Solana token address**.  
- **Sell tokens** with customizable options (percentage, limit orders).  
- **Automated limit orders** with **inline keyboard** controls.  
- Fetches **new token pairs** and sends real-time alerts.  

### 🔄 **Quick Swap & Sniper Mode**  
- **Instant token swapping** via Jupiter Aggregator API.  
- **Fast-track token sniping** on new launches.  
- **Tracks whale transactions** for trading insights.  

---

## ⚙️ Customization  

### 🔧 Modify Bot Behavior  
- All bot logic is in **`bot.js`**  
- API endpoints & trading functions are configurable.  
- Adjust **default slippage & transaction limits**.  

### 🚀 Future Upgrades  
- **More CEX & DEX integrations** for expanded trading.  
- **Advanced charting tools** for in-depth analysis.  
- **Automated portfolio tracking** & trading AI.  

---

## 📂 Project Structure  

```
📂 solanawizard_bot
│── 📂 src
│   ├── 📂 solana
│   │   ├── SolanaService.js  # Handles Solana blockchain interactions
│   │   ├── solanaTransactions.js  # Functions for SOL transfers and swaps
│   ├── 📂 transactions
│   │   ├── solanaTransactions.js  # Buy/sell logic and transaction handling
│   ├── 📂 services
│   │   ├── NewPairFetcher.js  # Listens for new Solana token pairs
│   │   ├── TelegramBot.js  # Handles Telegram interactions
│   ├── 📂 settings
│   │   ├── Settings.js  # User preferences and settings
│   ├── 📂 db
│   │   ├── FirebaseService.js  # Stores user wallet data securely
│   ├── 📂 help
│   │   ├── Help.js  # Help command and FAQs
│── index.js  # Main bot logic and event handling
│── package.json  # Dependencies and project info
│── .env  # Environment variables
```

---

🔥 **Solana Wizard Bot – Trade smarter, trade faster!** 🧙‍♂️💰  
