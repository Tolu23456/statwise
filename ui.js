// ui.js

/**
 * Creates interactive circle background animation that responds to cursor movement
 * @param {HTMLElement} container Optional container element, or creates one if null
 * @returns {Function} A cleanup function to stop the animation and remove listeners.
 */
function initInteractiveBackground(container = null) {
    console.log('🌀 Initializing interactive circles background...');
    
    // Create background container if not provided
    let createdElements = false;
    let backgroundContainer = container;
    let circles = [];
    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;
    let animationId;
    
    if (!backgroundContainer) {
        console.log('Creating interactive circles background...');
        
        // Create the animated background dynamically with theme-aware background
        backgroundContainer = document.createElement('div');
        backgroundContainer.className = 'animated-background-interactive';
        
        // Function to get theme-aware background color
        const getBackgroundColor = () => {
            const isDark = document.body.classList.contains('dark-mode');
            return isDark 
                ? getComputedStyle(document.documentElement).getPropertyValue('--primary-bg') || '#1a1d23'
                : getComputedStyle(document.documentElement).getPropertyValue('--primary-bg') || '#f8f9fc';
        };
        
        backgroundContainer.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            z-index: -1;
            pointer-events: none;
            overflow: hidden;
            background-color: ${getBackgroundColor()};
            transition: background-color 0.3s ease;
        `;
        
        // Create circles container
        const circlesContainer = document.createElement('div');
        circlesContainer.className = 'floating-circles-container';
        circlesContainer.style.cssText = `
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        `;
        
        // Create floating interactive circles
        const circleCount = 20;
        
        console.log(`Creating ${circleCount} interactive circles...`);
        
        for (let i = 0; i < circleCount; i++) {
            const circle = document.createElement('div');
            circle.className = 'floating-circle';
            
            // Random properties for each circle
            const size = Math.random() * 80 + 20; // 20px to 100px
            const startX = Math.random() * window.innerWidth;
            const startY = Math.random() * window.innerHeight;
            const speed = Math.random() * 1.5 + 0.5; // 0.5 to 2 speed multiplier
            // Increase base opacity for better visibility, especially in light mode
            const opacity = 0.2 + Math.random() * 0.4; // 0.2 to 0.6
            
            // Circle data object
            const circleData = {
                element: circle,
                x: startX,
                y: startY,
                vx: (Math.random() - 0.5) * 2, // Random horizontal velocity
                vy: (Math.random() - 0.5) * 2, // Random vertical velocity
                size: size,
                speed: speed,
                baseOpacity: opacity,
                colorType: Math.random() > 0.5 ? 'primary' : 'secondary' // Theme-aware color type
            };
            
            // Get theme-aware colors with better contrast for light mode
            const getThemeColor = (colorType) => {
                const isDark = document.body.classList.contains('dark-mode');
                if (colorType === 'primary') {
                    return isDark 
                        ? getComputedStyle(document.documentElement).getPropertyValue('--statwise-blue-light') || '#1976d2'
                        : getComputedStyle(document.documentElement).getPropertyValue('--statwise-blue') || '#0e639c';
                } else {
                    // For light mode secondary, use a darker color for better contrast
                    return isDark 
                        ? getComputedStyle(document.documentElement).getPropertyValue('--text-secondary') || '#b8bcc3'
                        : '#4a5568'; // Darker gray for better contrast in light mode
                }
            };
            
            const circleColor = getThemeColor(circleData.colorType);
            
            // Style the circle with theme colors
            circle.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                border-radius: 50%;
                background: radial-gradient(circle, 
                    ${circleColor}${Math.floor(opacity * 255).toString(16).padStart(2, '0')}, 
                    transparent
                );
                left: ${startX}px;
                top: ${startY}px;
                pointer-events: none;
                transition: opacity 0.3s ease, background 0.3s ease;
                will-change: transform, opacity;
                z-index: -1;
            `;
            
            circles.push(circleData);
            circlesContainer.appendChild(circle);
        }
        
        // Mouse tracking for interaction
        const handleMouseMove = (e) => {
            mouseX = e.clientX;
            mouseY = e.clientY;
        };
        
        // Touch support for mobile
        const handleTouchMove = (e) => {
            if (e.touches.length > 0) {
                mouseX = e.touches[0].clientX;
                mouseY = e.touches[0].clientY;
            }
        };
        
        // Function to update circle colors and background based on current theme
        const updateCircleColors = () => {
            // Update background container color
            const getBackgroundColor = () => {
                const isDark = document.body.classList.contains('dark-mode');
                return isDark 
                    ? getComputedStyle(document.documentElement).getPropertyValue('--primary-bg') || '#1a1d23'
                    : getComputedStyle(document.documentElement).getPropertyValue('--primary-bg') || '#f8f9fc';
            };
            backgroundContainer.style.backgroundColor = getBackgroundColor();
            
            // Update circle colors
            circles.forEach((circle) => {
                const getThemeColor = (colorType) => {
                    const isDark = document.body.classList.contains('dark-mode');
                    if (colorType === 'primary') {
                        return isDark 
                            ? getComputedStyle(document.documentElement).getPropertyValue('--statwise-blue-light') || '#1976d2'
                            : getComputedStyle(document.documentElement).getPropertyValue('--statwise-blue') || '#0e639c';
                    } else {
                        // For light mode secondary, use a darker color for better contrast
                        return isDark 
                            ? getComputedStyle(document.documentElement).getPropertyValue('--text-secondary') || '#b8bcc3'
                            : '#4a5568'; // Darker gray for better contrast in light mode
                    }
                };
                
                const circleColor = getThemeColor(circle.colorType);
                const opacity = circle.baseOpacity;
                
                // Update the background with new theme color
                circle.element.style.background = `radial-gradient(circle, 
                    ${circleColor}${Math.floor(opacity * 255).toString(16).padStart(2, '0')}, 
                    transparent
                )`;
            });
        };
        
        // Listen for theme changes
        const handleThemeChange = () => {
            updateCircleColors();
        };
        window.addEventListener('themechange', handleThemeChange);
        
        // Animation loop
        const animateCircles = () => {
            circles.forEach((circle, index) => {
                // Update position based on velocity
                circle.x += circle.vx * circle.speed;
                circle.y += circle.vy * circle.speed;
                
                // Bounce off edges
                if (circle.x <= 0 || circle.x >= window.innerWidth - circle.size) {
                    circle.vx *= -1;
                    circle.x = Math.max(0, Math.min(window.innerWidth - circle.size, circle.x));
                }
                if (circle.y <= 0 || circle.y >= window.innerHeight - circle.size) {
                    circle.vy *= -1;
                    circle.y = Math.max(0, Math.min(window.innerHeight - circle.size, circle.y));
                }
                
                // Calculate distance to mouse for interaction
                const dx = mouseX - (circle.x + circle.size / 2);
                const dy = mouseY - (circle.y + circle.size / 2);
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                // Mouse interaction effect
                const maxInteractionDistance = 200;
                if (distance < maxInteractionDistance) {
                    const influence = (maxInteractionDistance - distance) / maxInteractionDistance;
                    
                    // Gentle attraction/repulsion based on circle index
                    const attraction = index % 3 === 0 ? -0.02 : 0.01;
                    circle.vx += (dx / distance) * influence * attraction;
                    circle.vy += (dy / distance) * influence * attraction;
                    
                    // Increase opacity when near mouse
                    const interactOpacity = circle.baseOpacity + influence * 0.4;
                    circle.element.style.opacity = Math.min(interactOpacity, 0.8);
                } else {
                    // Return to base opacity when far from mouse
                    circle.element.style.opacity = circle.baseOpacity;
                }
                
                // Apply friction
                circle.vx *= 0.999;
                circle.vy *= 0.999;
                
                // Update DOM position
                circle.element.style.transform = `translate(${circle.x}px, ${circle.y}px)`;
            });
            
            if (createdElements) {
                animationId = requestAnimationFrame(animateCircles);
            }
        };
        
        // Handle window resize
        const handleResize = () => {
            circles.forEach(circle => {
                circle.x = Math.min(circle.x, window.innerWidth - circle.size);
                circle.y = Math.min(circle.y, window.innerHeight - circle.size);
            });
        };
        
        // Add event listeners
        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('touchmove', handleTouchMove);
        window.addEventListener('resize', handleResize);
        
        backgroundContainer.appendChild(circlesContainer);
        document.body.insertBefore(backgroundContainer, document.body.firstChild);
        createdElements = true;
        
        // Start animation
        animateCircles();
        
        console.log('✅ Interactive circles background created and animated');
    }

    // Enhanced cleanup function with proper variable checks
    return () => {
        if (backgroundContainer && createdElements) {
            createdElements = false;
            if (animationId) {
                cancelAnimationFrame(animationId);
                animationId = null;
            }
            // Remove event listeners with proper function references
            if (typeof handleMouseMove !== 'undefined') {
                document.removeEventListener('mousemove', handleMouseMove);
            }
            if (typeof handleTouchMove !== 'undefined') {
                document.removeEventListener('touchmove', handleTouchMove);
            }
            if (typeof handleResize !== 'undefined') {
                window.removeEventListener('resize', handleResize);
            }
            if (typeof handleThemeChange !== 'undefined') {
                window.removeEventListener('themechange', handleThemeChange);
            }
            if (backgroundContainer.parentNode) {
                backgroundContainer.remove();
            }
            circles = [];
            console.log('🧹 Interactive circles background cleaned up');
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
 * Applies the specified theme while preserving StatWise blue color scheme
 * @param {string} theme - 'light' or 'dark'
 */
function applyTheme(theme) {
    console.log(`🎨 Applying enhanced theme: ${theme}`);
    
    const body = document.body;
    const html = document.documentElement;
    
    // Remove existing theme classes
    body.classList.remove('light-mode', 'dark-mode');
    html.classList.remove('light-mode', 'dark-mode');
    
    // Apply new theme with enhanced styling
    if (theme === 'dark') {
        body.classList.add('dark-mode');
        html.classList.add('dark-mode');
        
        // Update CSS custom properties for dark theme while preserving blue
        document.documentElement.style.setProperty('--primary-bg', '#1a1d23');
        document.documentElement.style.setProperty('--secondary-bg', '#2a2f36');
        document.documentElement.style.setProperty('--card-bg', '#343a41');
        document.documentElement.style.setProperty('--text-primary', '#ffffff');
        document.documentElement.style.setProperty('--text-secondary', '#b8bcc3');
        document.documentElement.style.setProperty('--border-color', '#4a5159');
        // Preserve StatWise blue
        document.documentElement.style.setProperty('--statwise-blue', '#0e639c');
        document.documentElement.style.setProperty('--statwise-blue-light', '#1976d2');
        document.documentElement.style.setProperty('--statwise-blue-dark', '#0d5488');
        
        console.log('✅ Enhanced dark mode applied with preserved blue theme');
    } else {
        body.classList.add('light-mode');
        html.classList.add('light-mode');
        
        // Update CSS custom properties for light theme while preserving blue
        document.documentElement.style.setProperty('--primary-bg', '#f8f9fc');
        document.documentElement.style.setProperty('--secondary-bg', '#ffffff');
        document.documentElement.style.setProperty('--card-bg', '#ffffff');
        document.documentElement.style.setProperty('--text-primary', '#1a1a1c');
        document.documentElement.style.setProperty('--text-secondary', '#6b7280');
        document.documentElement.style.setProperty('--border-color', '#e5e7eb');
        // Preserve StatWise blue
        document.documentElement.style.setProperty('--statwise-blue', '#0e639c');
        document.documentElement.style.setProperty('--statwise-blue-light', '#1976d2');
        document.documentElement.style.setProperty('--statwise-blue-dark', '#0d5488');
        
        console.log('✅ Enhanced light mode applied with preserved blue theme');
    }
    
    // Save theme preference
    localStorage.setItem('statwise-theme', theme);
    
    console.log(`✅ Enhanced theme applied successfully: ${theme}`);
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

/**
 * Creates a new interactive background animation for auth pages
 * Animated floating geometric shapes (circles and squares) that move and react to mouse movement
 * Colors match app theme, no gradients
 * Returns a cleanup function
 */
function initAuthBackgroundAnimation() {
    // Remove any previous background
    const oldBg = document.getElementById('auth-bg-animation');
    if (oldBg) oldBg.remove();

    // Create canvas for animation
    const canvas = document.createElement('canvas');
    canvas.id = 'auth-bg-animation';
    canvas.style.position = 'fixed';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.width = '100vw';
    canvas.style.height = '100vh';
    canvas.style.zIndex = '-1';
    canvas.style.pointerEvents = 'none';
    document.body.appendChild(canvas);

    // Set canvas size
    function resizeCanvas() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Theme colors
    let bgColor = '#ffffff';
    let accentColor = getComputedStyle(document.documentElement).getPropertyValue('--statwise-accent') || '#0e639c';
    let shapeColor = getComputedStyle(document.documentElement).getPropertyValue('--statwise-shape') || '#0e639c';
    // Detect dark mode
    if (document.body.classList.contains('dark-mode')) {
        bgColor = '#2d2d30';
        accentColor = '#0e639c';
        shapeColor = '#ffffff';
    }

    // Generate shapes
    const shapes = [];
    const numShapes = 24;
    for (let i = 0; i < numShapes; i++) {
        shapes.push({
            x: Math.random() * canvas.width,
            y: Math.random() * canvas.height,
            size: 20 + Math.random() * 30,
            dx: (Math.random() - 0.5) * 1.5,
            dy: (Math.random() - 0.5) * 1.5,
            type: Math.random() > 0.5 ? 'circle' : 'square',
            color: Math.random() > 0.5 ? accentColor : shapeColor
        });
    }

    // Mouse interaction
    let mouseX = canvas.width / 2;
    let mouseY = canvas.height / 2;
    window.addEventListener('mousemove', e => {
        mouseX = e.clientX;
        mouseY = e.clientY;
    });

    // Animation loop
    let running = true;
    function animate() {
        if (!running) return;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        for (const s of shapes) {
            // Move shapes
            s.x += s.dx;
            s.y += s.dy;
            // Bounce off edges
            if (s.x < 0 || s.x > canvas.width) s.dx *= -1;
            if (s.y < 0 || s.y > canvas.height) s.dy *= -1;
            // Mouse repulsion
            const dist = Math.hypot(mouseX - s.x, mouseY - s.y);
            if (dist < 100) {
                const angle = Math.atan2(s.y - mouseY, s.x - mouseX);
                s.x += Math.cos(angle) * 2;
                s.y += Math.sin(angle) * 2;
            }
            // Draw shape
            ctx.save();
            ctx.globalAlpha = 0.7;
            ctx.fillStyle = s.color;
            if (s.type === 'circle') {
                ctx.beginPath();
                ctx.arc(s.x, s.y, s.size / 2, 0, Math.PI * 2);
                ctx.fill();
            } else {
                ctx.fillRect(s.x - s.size / 2, s.y - s.size / 2, s.size, s.size);
            }
            ctx.restore();
        }
        requestAnimationFrame(animate);
    }
    animate();

    // Cleanup function
    return function cleanup() {
        running = false;
        window.removeEventListener('resize', resizeCanvas);
        canvas.remove();
    };
}

export { initInteractiveBackground, initializeTheme, applyTheme, toggleTheme, initAuthBackgroundAnimation };