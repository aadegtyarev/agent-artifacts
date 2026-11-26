// A button with data-copy="<id>" copies the value of that element. A check
// mark shows for a moment; without clipboard access the text is selected.
for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", () => {
    const source = document.getElementById(button.dataset.copy);
    const copied = navigator.clipboard
      ? navigator.clipboard.writeText(source.value)
      : Promise.reject(new Error("no clipboard"));
    copied.then(
      () => {
        button.classList.add("done");
        setTimeout(() => button.classList.remove("done"), 1500);
      },
      () => {
        const details = source.closest("details");
        if (details) details.open = true;
        source.focus({ preventScroll: true });
        source.select();
      },
    );
  });
}
