(() => {
  "use strict";

  const periodSelect = document.getElementById("computerPeriod");
  const customDates = document.getElementById("computerCustomDates");

  if (periodSelect && customDates) {
    const updateCustomDates = () => {
      customDates.classList.toggle(
        "show",
        periodSelect.value === "custom"
      );
    };

    periodSelect.addEventListener("change", updateCustomDates);
    updateCustomDates();
  }

  const table = document.getElementById("computerReportTable");
  const body = document.getElementById("computerReportBody");

  if (!table || !body) {
    return;
  }

  const originalRows = Array.from(
    body.querySelectorAll("tr:not(.computer-empty-row)")
  );

  const searchInput = document.getElementById("computerTableSearch");
  const previousButton = document.getElementById("computerPreviousPage");
  const nextButton = document.getElementById("computerNextPage");
  const pageNumber = document.getElementById("computerPageNumber");
  const pageInfo = document.getElementById("computerPaginationInfo");
  const csvButton = document.getElementById("computerCsvButton");
  const printButton = document.getElementById("computerPrintButton");

  const rowsPerPage = 15;

  let filteredRows = [...originalRows];
  let currentPage = 1;
  let sortColumn = 0;
  let sortDirection = -1;

  const getCellValue = (row, index) => {
    const cell = row.cells[index];

    if (!cell) {
      return "";
    }

    return cell.dataset.value || cell.textContent.trim();
  };

  const renderRows = () => {
    originalRows.forEach((row) => {
      row.style.display = "none";
    });

    const totalPages = Math.max(
      1,
      Math.ceil(filteredRows.length / rowsPerPage)
    );

    currentPage = Math.min(
      Math.max(currentPage, 1),
      totalPages
    );

    const start = (currentPage - 1) * rowsPerPage;
    const visibleRows = filteredRows.slice(
      start,
      start + rowsPerPage
    );

    visibleRows.forEach((row) => {
      row.style.display = "table-row";
    });

    if (pageNumber) {
      pageNumber.textContent = String(currentPage);
    }

    if (previousButton) {
      previousButton.disabled = currentPage <= 1;
    }

    if (nextButton) {
      nextButton.disabled = currentPage >= totalPages;
    }

    if (pageInfo) {
      const from = filteredRows.length ? start + 1 : 0;
      const to = Math.min(
        start + rowsPerPage,
        filteredRows.length
      );

      pageInfo.textContent =
        `Showing ${from}–${to} of ${filteredRows.length} rows`;
    }
  };

  const applySearch = () => {
    const query = (
      searchInput?.value || ""
    ).trim().toLowerCase();

    filteredRows = originalRows.filter((row) =>
      row.textContent.toLowerCase().includes(query)
    );

    currentPage = 1;
    renderRows();
  };

  if (searchInput) {
    searchInput.addEventListener("input", applySearch);
  }

  if (previousButton) {
    previousButton.addEventListener("click", () => {
      currentPage -= 1;
      renderRows();
    });
  }

  if (nextButton) {
    nextButton.addEventListener("click", () => {
      currentPage += 1;
      renderRows();
    });
  }

  table.querySelectorAll("thead th").forEach(
    (header, index) => {
      header.addEventListener("click", () => {
        const dataType = header.dataset.type || "text";

        sortDirection =
          sortColumn === index
            ? sortDirection * -1
            : 1;

        sortColumn = index;

        filteredRows.sort((left, right) => {
          const leftRaw = getCellValue(left, index);
          const rightRaw = getCellValue(right, index);

          if (dataType === "number") {
            const leftNumber =
              Number(
                String(leftRaw).replace(
                  /[^0-9.-]/g,
                  ""
                )
              ) || 0;

            const rightNumber =
              Number(
                String(rightRaw).replace(
                  /[^0-9.-]/g,
                  ""
                )
              ) || 0;

            return (
              leftNumber - rightNumber
            ) * sortDirection;
          }

          if (dataType === "date") {
            return (
              new Date(leftRaw) -
              new Date(rightRaw)
            ) * sortDirection;
          }

          return (
            leftRaw.localeCompare(rightRaw) *
            sortDirection
          );
        });

        currentPage = 1;
        renderRows();
      });
    }
  );

  if (csvButton) {
    csvButton.addEventListener("click", () => {
      const headerRow = Array.from(
        table.querySelectorAll("thead th")
      ).map((cell) => cell.textContent.trim());

      const bodyRows = filteredRows.map((row) =>
        Array.from(row.cells).map((cell) =>
          cell.textContent.trim()
        )
      );

      const footerRow = Array.from(
        table.querySelectorAll("tfoot td")
      ).map((cell) => cell.textContent.trim());

      const rows = [
        headerRow,
        ...bodyRows,
        footerRow,
      ];

      const csv = rows
        .map((row) =>
          row
            .map((value) =>
              `"${String(value).replace(/"/g, '""')}"`
            )
            .join(",")
        )
        .join("\n");

      const blob = new Blob(
        ["\uFEFF" + csv],
        {
          type: "text/csv;charset=utf-8",
        }
      );

      const link = document.createElement("a");
      const fileUrl = URL.createObjectURL(blob);

      link.href = fileUrl;
      link.download =
        `ds-express-order-summary-${
          new Date().toISOString().slice(0, 10)
        }.csv`;

      document.body.appendChild(link);
      link.click();
      link.remove();

      URL.revokeObjectURL(fileUrl);
    });
  }

  if (printButton) {
    printButton.addEventListener(
      "click",
      () => window.print()
    );
  }

  renderRows();
})();