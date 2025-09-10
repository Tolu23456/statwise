// ui.js

/**
 * Creates a modern gradient wave background animation.
 * Simple and elegant replacement for the complex circle animation.
 * @param {HTMLElement} container Optional container element, or creates one if null
 * @returns {Function} A cleanup function to stop the animation and remove listeners.
 */
function initInteractiveBackground(container = null) {
    console.log('🌀 Initializing modern background...');
    
    // Create background container if not provided
    let createdElements = false;
    let backgroundContainer = container;
    
    if (!backgroundContainer) {
        console.log('Creating modern background elements...');
        
        // Create the animated background dynamically
        backgroundContainer = document.createElement('div');
        backgroundContainer.className = 'animated-background-interactive';
        backgroundContainer.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            pointer-events: none;
            overflow: hidden;
        `;
        
        // Create geometric shapes
        const shapesContainer = document.createElement('div');
        shapesContainer.className = 'geometric-shapes';
        shapesContainer.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        `;
        
        // Create floating geometric shapes
        const shapeCount = 8;
        const shapes = ['diamond', 'triangle', 'hexagon', 'square'];
        const colors = ['#0e639c', '#4caf50', '#ff9800', '#2196f3'];
        
        console.log(`Creating ${shapeCount} animated shapes...`);
        
        for (let i = 0; i < shapeCount; i++) {
            const shape = document.createElement('div');
            const shapeType = shapes[Math.floor(Math.random() * shapes.length)];
            const color = colors[Math.floor(Math.random() * colors.length)];
            const size = Math.random() * 60 + 30; // 30-90px
            const opacity = Math.random() * 0.15 + 0.05; // 0.05-0.2 opacity - very subtle
            const animationDuration = Math.random() * 20 + 15; // 15-35s very slow
            const animationDelay = Math.random() * 10; // 0-10s delay
            
            shape.className = `floating-shape ${shapeType}`;
            shape.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                background: ${color};
                opacity: ${opacity};
                left: ${Math.random() * 100}%;
                top: ${Math.random() * 100}%;
                pointer-events: none;
                animation: gentleFloat ${animationDuration}s infinite ease-in-out ${animationDelay}s;
                z-index: -1;
            `;
            
            // Apply shape-specific styles
            switch(shapeType) {
                case 'diamond':
                    shape.style.borderRadius = '8px';
                    // Use a different animation for diamonds to preserve rotation
                    shape.style.animation = `gentleFloatDiamond ${animationDuration}s infinite ease-in-out ${animationDelay}s`;
                    break;
                case 'triangle':
                    shape.style.clipPath = 'polygon(50% 0%, 0% 100%, 100% 100%)';
                    break;
                case 'hexagon':
                    shape.style.clipPath = 'polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%)';
                    break;
                case 'square':
                    shape.style.borderRadius = '12px';
                    break;
            }
            
            shapesContainer.appendChild(shape);
        }
        
        // Add CSS animation for floating shapes
        const style = document.createElement('style');
        style.textContent = `
            @keyframes gentleFloat {
                0%, 100% {
                    transform: translate(0, 0) rotate(0deg);
                }
                25% {
                    transform: translate(20px, -30px) rotate(90deg);
                }
                50% {
                    transform: translate(-15px, -60px) rotate(180deg);
                }
                75% {
                    transform: translate(-25px, -30px) rotate(270deg);
                }
            }
            
            @keyframes gentleFloatDiamond {
                0%, 100% {
                    transform: translate(0, 0) rotate(45deg);
                }
                25% {
                    transform: translate(20px, -30px) rotate(135deg);
                }
                50% {
                    transform: translate(-15px, -60px) rotate(225deg);
                }
                75% {
                    transform: translate(-25px, -30px) rotate(315deg);
                }
            }
            
            .animated-background-interactive {
                overflow: hidden !important;
                position: fixed !important;
                z-index: -10 !important;
                background: linear-gradient(135deg, 
                    rgba(14, 99, 156, 0.02) 0%,
                    rgba(76, 175, 80, 0.01) 25%,
                    rgba(33, 150, 243, 0.02) 50%,
                    rgba(14, 99, 156, 0.01) 100%);
            }
            
            .geometric-shapes {
                position: absolute !important;
                width: 100% !important;
                height: 100% !important;
            }
            
            .floating-shape {
                will-change: transform;
                position: absolute !important;
                filter: blur(0.5px);
            }
            
            .floating-shape.diamond {
                transform-origin: center center;
            }
            
            /* Responsive adjustments */
            @media (max-width: 768px) {
                .floating-shape {
                    width: 20px !important;
                    height: 20px !important;
                    opacity: 0.03 !important;
                }
            }
        `;
        document.head.appendChild(style);
        
        backgroundContainer.appendChild(shapesContainer);
        document.body.insertBefore(backgroundContainer, document.body.firstChild);
        createdElements = true;
        
        console.log('✅ Modern background elements created and added to DOM');
    }

    // Simple cleanup function for the new background
    return () => {
        if (backgroundContainer && createdElements) {
            backgroundContainer.remove();
            console.log('🧹 Modern background cleaned up');
        }
    };
}

/**
 * Initializes the theme system
 */
function initializeTheme() {
    console.log('🎨 Initializing theme system...');
    
    // Check for saved theme preference or default to 'light'
    const savedTheme = localStorage.getItem('statwise-theme') || 'light';
    console.log('Current saved theme:', savedTheme);
    
    applyTheme(savedTheme);
    
    // Listen for theme changes
    window.addEventListener('themechange', (e) => {
        applyTheme(e.detail.theme);
    });
    
    console.log('✅ Theme system initialized successfully');
}

/**
 * Applies the specified theme
 * @param {string} theme - 'light' or 'dark'
 */
function applyTheme(theme) {
    console.log(`🎨 Applying theme: ${theme}`);
    
    const body = document.body;
    const html = document.documentElement;
    
    // Remove existing theme classes
    body.classList.remove('light-mode', 'dark-mode');
    html.classList.remove('light-mode', 'dark-mode');
    
    // Apply new theme
    if (theme === 'dark') {
        body.classList.add('dark-mode');
        html.classList.add('dark-mode');
        console.log('✅ Dark mode applied to body and html');
    } else {
        body.classList.add('light-mode');
        html.classList.add('light-mode');
        console.log('✅ Light mode applied to body and html');
    }
    
    // Save theme preference
    localStorage.setItem('statwise-theme', theme);
    
    console.log(`✅ Theme applied successfully: ${theme}`);
}

/**
 * Toggles between light and dark themes
 */
function toggleTheme() {
    const currentTheme = localStorage.getItem('statwise-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    applyTheme(newTheme);
    
    // Dispatch theme change event
    window.dispatchEvent(new CustomEvent('themechange', {
        detail: { theme: newTheme }
    }));
    
    return newTheme;
}

export { initInteractiveBackground, initializeTheme, applyTheme, toggleTheme };