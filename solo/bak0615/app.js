const state = {
    darkMode: false,
    autoReply: true,
    replyDelay: 1000,
    currentPage: 'chat'
};

const autoReplies = [
    "收到！让我想想...",
    "这是个好问题！",
    "我理解你的意思。",
    "好的，我来帮你处理。",
    "很有趣的想法！",
    "让我为你详细解答。",
    "明白了，我们继续。",
    "谢谢你的消息！",
    "这个想法很棒！",
    "我正在思考中..."
];

const elements = {
    messageInput: document.getElementById('messageInput'),
    sendBtn: document.getElementById('sendBtn'),
    messagesContainer: document.getElementById('messagesContainer'),
    toggleTheme: document.getElementById('toggleTheme'),
    darkModeSwitch: document.getElementById('darkModeSwitch'),
    autoReplySwitch: document.getElementById('autoReplySwitch'),
    replyDelay: document.getElementById('replyDelay'),
    delayValue: document.getElementById('delayValue'),
    chatPage: document.getElementById('chatPage'),
    settingsPage: document.getElementById('settingsPage'),
    navItems: document.querySelectorAll('.nav-item')
};

function init() {
    loadState();
    applyTheme();
    bindEvents();
}

function loadState() {
    const saved = localStorage.getItem('chatAppState');
    if (saved) {
        Object.assign(state, JSON.parse(saved));
    }
    updateUIFromState();
}

function saveState() {
    localStorage.setItem('chatAppState', JSON.stringify(state));
}

function updateUIFromState() {
    elements.darkModeSwitch.checked = state.darkMode;
    elements.autoReplySwitch.checked = state.autoReply;
    elements.replyDelay.value = state.replyDelay;
    elements.delayValue.textContent = state.replyDelay;
}

function applyTheme() {
    if (state.darkMode) {
        document.body.classList.add('dark');
    } else {
        document.body.classList.remove('dark');
    }
}

function toggleTheme() {
    state.darkMode = !state.darkMode;
    applyTheme();
    updateUIFromState();
    saveState();
}

function bindEvents() {
    elements.sendBtn.addEventListener('click', sendMessage);
    elements.messageInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });
    elements.toggleTheme.addEventListener('click', toggleTheme);
    elements.darkModeSwitch.addEventListener('change', () => {
        state.darkMode = elements.darkModeSwitch.checked;
        applyTheme();
        saveState();
    });
    elements.autoReplySwitch.addEventListener('change', () => {
        state.autoReply = elements.autoReplySwitch.checked;
        saveState();
    });
    elements.replyDelay.addEventListener('input', () => {
        state.replyDelay = parseInt(elements.replyDelay.value);
        elements.delayValue.textContent = state.replyDelay;
        saveState();
    });
    elements.navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            navigateTo(page);
        });
    });
}

function navigateTo(page) {
    state.currentPage = page;
    elements.navItems.forEach(item => {
        item.classList.toggle('active', item.dataset.page === page);
    });
    elements.chatPage.style.display = page === 'chat' ? 'flex' : 'none';
    elements.settingsPage.style.display = page === 'settings' ? 'flex' : 'none';
    saveState();
}

function getCurrentTime() {
    const now = new Date();
    return now.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function createMessageElement(content, role) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    
    const avatar = role === 'user' ? '👤' : '🤖';
    
    messageDiv.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div class="message-content">
            <div class="message-text">${escapeHtml(content)}</div>
            <div class="message-time">${getCurrentTime()}</div>
        </div>
    `;
    
    return messageDiv;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function addMessage(content, role) {
    const messageEl = createMessageElement(content, role);
    elements.messagesContainer.appendChild(messageEl);
    scrollToBottom();
}

function scrollToBottom() {
    elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
}

function getRandomReply() {
    return autoReplies[Math.floor(Math.random() * autoReplies.length)];
}

function sendMessage() {
    const message = elements.messageInput.value.trim();
    if (!message) return;
    
    addMessage(message, 'user');
    elements.messageInput.value = '';
    
    if (state.autoReply) {
        setTimeout(() => {
            addMessage(getRandomReply(), 'assistant');
        }, state.replyDelay);
    }
}

init();
