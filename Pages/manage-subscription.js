document.getElementById('cancelSubscription').addEventListener('click', () => {
    if (confirm('Are you sure you want to cancel your subscription?')) {
        alert('Your subscription has been canceled.');
        // Here you would add the logic to actually cancel the subscription
        // For example, make an API call to your backend.
    }
});

document.getElementById('changePlan').addEventListener('click', () => {
    alert('Redirecting to change plan page...');
    // Here you would redirect the user to the change plan page
});
