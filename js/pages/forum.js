import { supabase } from '../../env.js';
import { showModal } from '../../utils.js';

let currentUser;

export async function initializeForumPage(user) {
    currentUser = user;
    const messageInput = document.getElementById('messageInput');
    const sendMessageBtn = document.getElementById('sendMessageBtn');
    const charCounter = document.getElementById('charCounter');

    if (messageInput && sendMessageBtn && charCounter) {
        messageInput.addEventListener('input', () => {
            const message = messageInput.value.trim();
            sendMessageBtn.disabled = message.length === 0;
            charCounter.textContent = 500 - message.length;
        });

        sendMessageBtn.addEventListener('click', async () => {
            const message = messageInput.value.trim();
            if (message) {
                await sendMessage(message);
                messageInput.value = '';
                sendMessageBtn.disabled = true;
                charCounter.textContent = 500;
            }
        });
    }

    await loadMessages();
    setupMessageListener();
}

async function sendMessage(message) {
    if (!currentUser) {
        showModal({
            message: 'You must be logged in to send messages.',
            confirmText: 'OK'
        });
        return;
    }

    const { error } = await supabase
        .from('forum_messages')
        .insert([{
            user_id: currentUser.id,
            username: currentUser.user_metadata?.display_name || currentUser.email.split('@')[0],
            message: message
        }]);

    if (error) {
        console.error('Error sending message:', error);
        showModal({
            message: 'Failed to send message. Please try again.',
            confirmText: 'OK'
        });
    }
}

async function loadMessages() {
    const messagesContainer = document.getElementById('messagesContainer');
    if (!messagesContainer) return;

    const { data: messages, error } = await supabase
        .from('forum_messages')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(50);

    if (error) {
        console.error('Error loading messages:', error);
        messagesContainer.innerHTML = '<p class="error-message">Failed to load messages.</p>';
        return;
    }

    if (messages.length === 0) {
        messagesContainer.innerHTML = `
            <div class="empty-forum">
                <div class="empty-icon">👋</div>
                <h3>Be the first to start a conversation!</h3>
                <p>The forum is empty right now. Share your thoughts, predictions, or just say hello.</p>
            </div>
        `;
        return;
    }

    messagesContainer.innerHTML = messages.map(message => createMessageHTML(message)).join('');
}

function createMessageHTML(message) {
    const isOwnMessage = currentUser && message.user_id === currentUser.id;
    return `
        <div class="post-card ${isOwnMessage ? 'own-post' : ''}">
            <div class="post-header">
                <div class="profile-avatar">
                    <div class="avatar-placeholder">${(message.username || 'U').charAt(0)}</div>
                </div>
                <div class="post-info">
                    <p class="username">${message.username}</p>
                    <p class="timestamp">${new Date(message.created_at).toLocaleString()}</p>
                </div>
            </div>
            <div class="post-content">
                <p>${message.message}</p>
            </div>
        </div>
    `;
}

function setupMessageListener() {
    supabase
        .channel('public:forum_messages')
        .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'forum_messages' }, (payload) => {
            const newMessage = payload.new;
            const messagesContainer = document.getElementById('messagesContainer');
            if (messagesContainer) {
                // If the container had the "empty" message, remove it
                const emptyMessage = messagesContainer.querySelector('.empty-forum');
                if (emptyMessage) {
                    emptyMessage.remove();
                }
                messagesContainer.insertAdjacentHTML('afterbegin', createMessageHTML(newMessage));
            }
        })
        .subscribe();
}
