// ui.js

/**
 * Creates an interactive, floating circle background.
 * @param {HTMLElement} container The '.area' element containing the animation.
 * @returns {Function} A cleanup function to stop the animation and remove listeners.
 */
function initInteractiveBackground(container) {
    const circlesList = container.querySelector('.circles');
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
        const size = parseFloat(getComputedStyle(el).width);
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
        el.style.left = '0'; el.style.top = '0';
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

            p.pushX *= p.friction; p.pushY *= p.friction;
            p.x += p.vx + p.pushX; p.y -= p.vy;

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
    };
}

export { initInteractiveBackground };