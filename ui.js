// ui.js

/**
 * Creates an interactive, floating circle background.
 * If no container is provided, creates the background elements dynamically.
 * @param {HTMLElement} container Optional container element, or creates one if null
 * @returns {Function} A cleanup function to stop the animation and remove listeners.
 */
function initInteractiveBackground(container = null) {
    // Create background container and circles if not provided
    let createdElements = false;
    let backgroundContainer = container;
    
    if (!backgroundContainer) {
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
        const circleCount = 15;
        const colors = ['#0e639c', '#4caf50', '#ff9800', '#e91e63', '#9c27b0'];
        
        for (let i = 0; i < circleCount; i++) {
            const li = document.createElement('li');
            const size = Math.random() * 60 + 20; // 20-80px
            const color = colors[Math.floor(Math.random() * colors.length)];
            const opacity = Math.random() * 0.3 + 0.1; // 0.1-0.4
            
            li.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                background: ${color};
                border-radius: 50%;
                opacity: ${opacity};
                animation: float ${Math.random() * 20 + 10}s infinite linear;
                left: ${Math.random() * 100}%;
                top: 100%;
                pointer-events: none;
            `;
            
            circlesList.appendChild(li);
        }
        
        backgroundContainer.appendChild(circlesList);
        document.body.insertBefore(backgroundContainer, document.body.firstChild);
        createdElements = true;
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
    // Check for saved theme preference or default to 'light'
    const savedTheme = localStorage.getItem('statwise-theme') || 'light';
    applyTheme(savedTheme);
    
    // Listen for theme changes
    window.addEventListener('themechange', (e) => {
        applyTheme(e.detail.theme);
    });
}

/**
 * Applies the specified theme
 * @param {string} theme - 'light' or 'dark'
 */
function applyTheme(theme) {
    const body = document.body;
    const html = document.documentElement;
    
    // Remove existing theme classes
    body.classList.remove('light-mode', 'dark-mode');
    html.classList.remove('light-mode', 'dark-mode');
    
    // Apply new theme
    if (theme === 'dark') {
        body.classList.add('dark-mode');
        html.classList.add('dark-mode');
    } else {
        body.classList.add('light-mode');
        html.classList.add('light-mode');
    }
    
    // Save theme preference
    localStorage.setItem('statwise-theme', theme);
    
    console.log(`Theme applied: ${theme}`);
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