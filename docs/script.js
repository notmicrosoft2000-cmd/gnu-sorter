window.addEventListener('scroll', () => {
    const nav = document.querySelector('.nav');
    if (window.scrollY > 50) {
        nav.classList.add('scrolled');
    } else {
        nav.classList.remove('scrolled');
    }
});

// Add subtle parallax effect to showcase
const showcase = document.querySelector('.showcase-img');
if (showcase) {
    window.addEventListener('mousemove', (e) => {
        const x = (window.innerWidth - e.pageX) / 50;
        const y = (window.innerHeight - e.pageY) / 50;
        showcase.style.transform = `perspective(1000px) rotateY(${x}deg) rotateX(${-y}deg) translateZ(20px)`;
    });
    
    showcase.addEventListener('mouseleave', () => {
        showcase.style.transform = 'perspective(1000px) rotateY(0deg) rotateX(0deg) translateZ(0px)';
        showcase.style.transition = 'all 0.5s ease';
    });
    
    showcase.addEventListener('mouseenter', () => {
        showcase.style.transition = 'none';
    });
}
