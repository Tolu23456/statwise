
// utils.js
import { supabase } from './env.js';

/**
 * Fetches the user's public IP address.
 * @returns {Promise<string>} The public IP or "Unknown".
 */
export async function getPublicIP() {
    try {
        const res = await fetch("https://api.ipify.org?format=json");
        if (!res.ok) return "Unknown";
        const data = await res.json();
        return data.ip || "Unknown";
    } catch {
        return "Unknown";
    }
}

/**
 * Formats a timestamp string or Date into a "DD/MM/YYYY HH:mm" string.
 * @param {string|Date} timestamp - The timestamp to format.
 * @returns {string} The formatted date string or an empty string.
 */
export function formatTimestamp(timestamp) {
    try {
        if (!timestamp) return "";
        
        let date;
        if (typeof timestamp === 'string') {
            date = new Date(timestamp);
        } else if (timestamp instanceof Date) {
            date = timestamp;
        } else if (timestamp?.toDate) {
            // Firestore Timestamp object
            date = timestamp.toDate();
        } else {
            return "";
        }
        
        if (isNaN(date.getTime())) return "";
        
        const day = String(date.getDate()).padStart(2, "0");
        const month = String(date.getMonth() + 1).padStart(2, "0");
        const year = date.getFullYear();
        const hours = String(date.getHours()).padStart(2, "0");
        const mins = String(date.getMinutes()).padStart(2, "0");
        return `${day}/${month}/${year} ${hours}:${mins}`;
    } catch (error) {
        console.warn('Error formatting timestamp:', error);
        return "";
    }
}

/**
 * Adds a unique action to the user's history log via Supabase.
 * @param {string} userId - The user's ID.
 * @param {string} action - The action description to log.
 */
export async function addHistoryUnique(userId, action) {
    if (!userId || !action) return;
    
    try {
        // Get the user's last history entry to prevent duplicates
        const { data: lastEntry, error: fetchError } = await supabase
            .from('user_history')
            .select('action')
            .eq('user_id', userId)
            .order('created_at', { ascending: false })
            .limit(1)
            .single();
        
        // Prevent logging the exact same action back-to-back
        if (lastEntry && lastEntry.action === action) return;
        
        const ip = await getPublicIP();
        const { error } = await supabase
            .from('user_history')
            .insert({
                user_id: userId,
                action: action,
                ip_address: ip,
                created_at: new Date().toISOString()
            });
            
        if (error) {
            console.warn('Failed to add history:', error);
        }
    } catch (err) {
        console.error("Failed to add history:", err);
    }
}

export function showModal(options) {
    try {
        // Validate options
        if (!options || typeof options !== 'object') {
            console.error('Invalid modal options');
            return;
        }

        const modal = document.createElement('div');
        modal.className = 'modal-overlay';

        // Support both 'inputValue' (preferred) and legacy 'inputVal'
        const modalInputValue = options.inputValue ?? options.inputVal ?? '';
        const inputFieldHTML = options.inputType ? `
            <div class="modal-input-wrapper">
                <input type="${options.inputType}" class="modal-input" value="${modalInputValue}" placeholder="${options.inputPlaceholder || ''}">
            </div>
        ` : '';

        // Escape HTML to prevent XSS
        const safeMessage = options.message ? String(options.message).replace(/</g, '&lt;').replace(/>/g, '&gt;') : '';

        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-message">${safeMessage}</div>
                ${inputFieldHTML}
                <div class="modal-actions">
                    ${options.cancelText ? `<button class="btn-cancel">${options.cancelText}</button>` : ''}
                    <button class="btn-confirm ${options.confirmClass || 'btn-primary'}">${options.confirmText || 'OK'}</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const confirmBtn = modal.querySelector('.btn-confirm');
        const cancelBtn = modal.querySelector('.btn-cancel');
        const inputField = modal.querySelector('.modal-input');

        // Focus input if it exists
        if (inputField) {
            setTimeout(() => {
                try {
                    inputField.focus();
                } catch (focusError) {
                    console.warn('Could not focus input field:', focusError);
                }
            }, 100);
        }

        const cleanup = () => {
            try {
                if (modal && modal.parentNode) {
                    document.body.removeChild(modal);
                }
            } catch (cleanupError) {
                console.warn('Error cleaning up modal:', cleanupError);
            }
        };

        if (confirmBtn) {
            confirmBtn.addEventListener('click', () => {
                try {
                    const inputValue = inputField ? inputField.value : null;
                    cleanup();
                    if (options.onConfirm) {
                        if (inputField) {
                            options.onConfirm(inputValue);
                        } else {
                            options.onConfirm();
                        }
                    }
                } catch (confirmError) {
                    console.error('Error in modal confirm:', confirmError);
                    cleanup();
                }
            });
        }

        if (cancelBtn) {
            cancelBtn.addEventListener('click', () => {
                try {
                    cleanup();
                    if (options.onCancel) options.onCancel();
                } catch (cancelError) {
                    console.error('Error in modal cancel:', cancelError);
                }
            });
        }

        // Handle Enter key for input
        if (inputField) {
            inputField.addEventListener('keypress', (e) => {
                if (e.key === 'Enter' && confirmBtn) {
                    confirmBtn.click();
                }
            });
        }

        // Close on overlay click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                try {
                    cleanup();
                    if (options.onCancel) options.onCancel();
                } catch (overlayError) {
                    console.error('Error in modal overlay click:', overlayError);
                }
            }
        });

    } catch (error) {
        console.error('Error creating modal:', error);
    }
}
