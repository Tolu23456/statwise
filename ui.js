// ui.js

/**
 * Creates an interactive, floating circle background.
 * If no container is provided, creates the background elements dynamically.
 * @param {HTMLElement} container Optional container element, or creates one if null
 * @returns {Function} A cleanup function to stop the animation and remove listeners.
 */
function initInteractiveBackground(container = null) {
    console.log('🌀 Initializing interactive background...');
    
    // Create background container and circles if not provided
    let createdElements = false;
    let backgroundContainer = container;
    
    if (!backgroundContainer) {
        console.log('Creating dynamic background elements...');
        
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
        
        // Create circles container
        const circlesList = document.createElement('ul');
        circlesList.className = 'circles';
        circlesList.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            list-style: none;
            margin: 0;
            padding: 0;
        `;
        
        // Create floating circles
        const circleCount = 20;
        const colors = ['#0e639c', '#4caf50', '#ff9800', '#e91e63', '#9c27b0', '#2196f3', '#ffc107'];
        
        console.log(`Creating ${circleCount} animated circles...`);
        
        for (let i = 0; i < circleCount; i++) {
            const li = document.createElement('li');
            const size = Math.random() * 80 + 30; // 30-110px
            const color = colors[Math.floor(Math.random() * colors.length)];
            const opacity = Math.random() * 0.4 + 0.2; // 0.2-0.6
            const animationDuration = Math.random() * 15 + 8; // 8-23s
            const animationDelay = Math.random() * 5; // 0-5s delay
            
            li.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                background: ${color};
                border-radius: 50%;
                opacity: ${opacity};
                left: ${Math.random() * 100}%;
                top: 100vh;
                pointer-events: none;
                animation: floatUp ${animationDuration}s infinite linear ${animationDelay}s;
                transform: translateY(0);
                z-index: -1;
            `;
            
            circlesList.appendChild(li);
        }
        
        // Add CSS animation for floating circles
        const style = document.createElement('style');
        style.textContent = `
            @keyframes floatUp {
                0% {
                    transform: translateY(50px) scale(0.3);
                    opacity: 0;
                }
                5% {
                    opacity: 0.6;
                    transform: translateY(0) scale(0.5);
                }
                25% {
                    opacity: 0.8;
                    transform: translateY(-25vh) scale(0.8);
                }
                50% {
                    opacity: 1;
                    transform: translateY(-50vh) scale(1);
                }
                75% {
                    opacity: 0.8;
                    transform: translateY(-75vh) scale(0.8);
                }
                95% {
                    opacity: 0.4;
                    transform: translateY(-100vh) scale(0.5);
                }
                100% {
                    transform: translateY(-110vh) scale(0);
                    opacity: 0;
                }
            }
            
            .animated-background-interactive {
                overflow: hidden !important;
            }
            
            .circles li {
                will-change: transform, opacity;
            }
        `;
        document.head.appendChild(style);
        
        backgroundContainer.appendChild(circlesList);
        document.body.insertBefore(backgroundContainer, document.body.firstChild);
        createdElements = true;
        
        console.log('✅ Background elements created and added to DOM');
    }

    const circlesList = backgroundContainer.querySelector('.circles') || backgroundContainer.querySelector('ul');
    if (!circlesList) return () => {};

    let animationFrameId;
    const circleElements = Array.from(circlesList.querySelectorAll('li'));
    
    const cursor = { x: 9999, y: 9999 };
    const interactionRadius = 150;

    const mouseMoveHandler = e => { cursor.x = e.clientX; cursor.y = e.clientY; };
    const touchMoveHandler = e => {
        if (e.touches.length > 0) { cursor.x = e.touches[0].clientX; cursor.y = e.touches[0].clientY; }
    };
    const mouseOutHandler = () => { cursor.x = 9999; cursor.y = 9999; };

    document.addEventListener('mousemove', mouseMoveHandler);
    document.addEventListener('touchstart', touchMoveHandler, { passive: true });
    document.addEventListener('touchmove', touchMoveHandler, { passive: true });
    document.addEventListener('mouseout', mouseOutHandler);
    document.addEventListener('touchend', mouseOutHandler);

    const circleObjects = circleElements.map(el => {
        const size = parseFloat(getComputedStyle(el).width) || 40;
        return {
            el: el,
            x: Math.random() * window.innerWidth,
            y: window.innerHeight + size + Math.random() * 200,
            radius: size / 2,
            vy: Math.random() * 1 + 0.5, // Upward speed
            vx: (Math.random() - 0.5) * 0.5, // Sideways drift
            pushX: 0, pushY: 0,
            friction: 0.95 // for smooth return
        };
    });

    circleElements.forEach(el => {
        el.style.position = 'absolute';
        el.style.left = '0'; 
        el.style.top = '0';
        el.style.borderRadius = '50%';
    });

    function animate() {
        circleObjects.forEach(p => {
            const dx = p.x - cursor.x;
            const dy = p.y - cursor.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist < interactionRadius) {
                const angle = Math.atan2(dy, dx);
                const force = (interactionRadius - dist) / interactionRadius;
                p.pushX += Math.cos(angle) * force * 0.6;
                p.pushY += Math.sin(angle) * force * 0.6;
            }

            p.pushX *= p.friction; 
            p.pushY *= p.friction;
            p.x += p.vx + p.pushX; 
            p.y -= p.vy;

            if (p.y < -p.radius * 2) {
                p.x = Math.random() * window.innerWidth;
                p.y = window.innerHeight + p.radius * 2;
            }
            p.el.style.transform = `translate(${p.x - p.radius}px, ${p.y - p.radius}px)`;
        });
        animationFrameId = requestAnimationFrame(animate);
    }
    animate();

    return () => {
        if (animationFrameId) cancelAnimationFrame(animationFrameId);
        document.removeEventListener('mousemove', mouseMoveHandler);
        document.removeEventListener('touchstart', touchMoveHandler);
        document.removeEventListener('touchmove', touchMoveHandler);
        document.removeEventListener('mouseout', mouseOutHandler);
        document.removeEventListener('touchend', mouseOutHandler);
        
        // Clean up created elements
        if (createdElements && backgroundContainer) {
            backgroundContainer.remove();
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