# AI-Powered Letter Drafting System - Setup Guide

## 🚀 Quick Start

### Prerequisites

1. **MongoDB** - Database server
2. **OpenAI API Key** - For AI functionality
3. **Python 3.8+** - Backend runtime
4. **Node.js 16+** - Frontend runtime

### 1. Database Setup

#### Option A: Install MongoDB Locally

```bash
# Windows (using Chocolatey)
choco install mongodb

# macOS (using Homebrew)
brew tap mongodb/brew
brew install mongodb-community

# Ubuntu/Debian
sudo apt-get install mongodb

# Start MongoDB service
mongod --dbpath /path/to/your/db
```

#### Option B: Use MongoDB Atlas (Cloud)

1. Create account at [MongoDB Atlas](https://www.mongodb.com/atlas)
2. Create a cluster
3. Get connection string
4. Update `DATABASE_URL` in backend config

### 2. Backend Setup

```bash
# Navigate to backend directory
cd backend

# Create .env file from template
cp .env.example .env

# Edit .env file and add your OpenAI API key
# OPENAI_API_KEY=your_actual_api_key_here

# Install dependencies (if not already installed)
pip install -r requirements.txt

# Start the backend server
uvicorn rbac_backend.main:app --reload
```

### 3. Frontend Setup

```bash
# Navigate to client directory
cd client

# Install dependencies (if not already installed)
npm install

# Start the frontend development server
npm run dev
```

### 4. OpenAI API Key Setup

1. Visit [OpenAI Platform](https://platform.openai.com/api-keys)
2. Create an account or sign in
3. Generate a new API key
4. Add it to your `backend/.env` file:
   ```
   OPENAI_API_KEY=sk-your-actual-key-here
   ```

## 🎯 Using the AI Assistant

### Access the AI Assistant

1. Navigate to **Letter Workflow** page
2. Click on the **AI Assistant** tab
3. Use the interface to:
   - Search for similar letters
   - Generate AI-powered drafts
   - Apply drafts to the editor

### AI Workflow

1. **Search Similar Letters**: Enter keywords or topic
2. **Review Results**: See semantically similar letters
3. **Generate Draft**: AI creates professional letter
4. **Edit & Refine**: Modify the generated content
5. **Save & Submit**: Enter standard workflow process

## 🔧 Features

### Backend API Endpoints

- `GET /api/ai-assistant/search-letters` - Semantic search
- `POST /api/ai-assistant/generate-draft` - AI draft generation
- `GET /api/ai-assistant/letter-history` - View AI history
- `GET /api/letters` - Letter management
- `POST /api/letters` - Create new letters

### Frontend Components

- **AIAssistant.tsx** - Main AI interface
- **LetterDraftEditor** - Enhanced with AI integration
- **LetterWorkflowPage** - AI Assistant tab

### Database Collections

- `letters` - Letter storage with embeddings
- `ai_generated_drafts` - AI generation history
- `documents` - Cross-reference documents

## 🛠️ Troubleshooting

### MongoDB Connection Issues

```
Error: ServerSelectionTimeoutError: localhost:27017
```

**Solution**: Start MongoDB service

```bash
# Windows
net start MongoDB

# macOS/Linux
sudo systemctl start mongod
# or
mongod --dbpath /path/to/db
```

### OpenAI API Issues

```
Error: OpenAI API key not found
```

**Solution**: Add API key to `.env` file

```
OPENAI_API_KEY=sk-your-key-here
```

### Import Errors

```
ModuleNotFoundError: No module named 'backend'
```

**Solution**: Run from correct directory

```bash
cd backend
uvicorn rbac_backend.main:app --reload
```

## 📋 System Requirements

### Minimum Requirements

- **RAM**: 4GB
- **Storage**: 2GB free space
- **Network**: Internet connection for OpenAI API

### Recommended Requirements

- **RAM**: 8GB+
- **Storage**: 5GB+ free space
- **CPU**: Multi-core processor

## 🔐 Security Notes

1. **API Keys**: Never commit API keys to version control
2. **Environment Variables**: Use `.env` files for sensitive data
3. **Database**: Secure MongoDB with authentication in production
4. **HTTPS**: Use HTTPS in production environments

## 📚 Additional Resources

- [OpenAI API Documentation](https://platform.openai.com/docs)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [React Documentation](https://reactjs.org/docs)

## 🆘 Support

If you encounter issues:

1. Check the troubleshooting section above
2. Verify all prerequisites are installed
3. Ensure environment variables are set correctly
4. Check server logs for detailed error messages
