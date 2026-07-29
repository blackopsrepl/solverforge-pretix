(function () {
    "use strict";

    function svgElement(name, attributes) {
        const element = document.createElementNS("http://www.w3.org/2000/svg", name);
        Object.entries(attributes || {}).forEach(([key, value]) => {
            element.setAttribute(key, String(value));
        });
        return element;
    }

    function partyColor(key) {
        let hash = 0;
        for (const character of key) {
            hash = ((hash << 5) - hash + character.codePointAt(0)) | 0;
        }
        return `hsl(${Math.abs(hash) % 360} 58% 72%)`;
    }

    function coordinateScale(seats) {
        const uniqueXs = [...new Set(seats.map((seat) => Number(seat.x)))].sort(
            (left, right) => left - right,
        );
        const gaps = uniqueXs
            .slice(1)
            .map((value, index) => value - uniqueXs[index])
            .filter((value) => value > 0)
            .sort((left, right) => left - right);
        if (!gaps.length) {
            return 1;
        }
        const ordinaryPitch = gaps[Math.floor((gaps.length - 1) / 2)];
        return Math.min(34, Math.max(0.2, 34 / ordinaryPitch));
    }

    function seatDescription(seat) {
        const parts = [`${seat.zone}, row ${seat.row}, seat ${seat.number}`];
        if (seat.blocked) {
            parts.push("blocked");
        } else if (seat.unavailable) {
            parts.push("unavailable");
        } else {
            parts.push("available");
        }
        if (seat.existing_order) {
            parts.push(`existing assignment ${seat.existing_order}`);
        }
        if (seat.proposed) {
            parts.push(
                seat.proposed.committed
                    ? `assigned to order ${seat.proposed.order_code}`
                    : `proposed for order ${seat.proposed.order_code}`,
            );
        }
        if (seat.locked) {
            parts.push("locked");
        }
        if (seat.accessible) {
            parts.push("wheelchair accessible");
        }
        return parts.join(", ");
    }

    function renderSeatMap(container, seats) {
        if (!seats.length) {
            container.textContent = "No seats to display.";
            return;
        }
        const xs = seats.map((seat) => Number(seat.x));
        const ys = seats.map((seat) => Number(seat.y));
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const scale = coordinateScale(seats);
        const margin = 52;
        const width = Math.max(680, (maxX - minX) * scale + margin * 2);
        const height = Math.max(350, (maxY - minY) * scale + margin * 2);
        const svg = svgElement("svg", {
            viewBox: `0 0 ${width} ${height}`,
            role: "img",
            "aria-label": "SolverForge seating proposal map",
        });

        const zones = new Map();
        seats.forEach((seat) => {
            const zoneSeats = zones.get(seat.zone) || [];
            zoneSeats.push(seat);
            zones.set(seat.zone, zoneSeats);
        });
        zones.forEach((zoneSeats, zone) => {
            const zoneX = Math.min(...zoneSeats.map((seat) => Number(seat.x)));
            const zoneY = Math.min(...zoneSeats.map((seat) => Number(seat.y)));
            const label = svgElement("text", {
                x: (zoneX - minX) * scale + margin,
                y: (zoneY - minY) * scale + margin - 22,
                class: "sf-zone-label",
            });
            label.textContent = zone;
            svg.appendChild(label);
        });

        seats.forEach((seat) => {
            const x = (Number(seat.x) - minX) * scale + margin;
            const y = (Number(seat.y) - minY) * scale + margin;
            const classes = ["sf-seat"];
            if (seat.blocked) {
                classes.push("sf-seat--blocked");
            }
            if (seat.unavailable) {
                classes.push("sf-seat--unavailable");
            }
            if (seat.existing_order) {
                classes.push("sf-seat--existing");
            }
            if (seat.proposed) {
                classes.push(
                    seat.proposed.committed
                        ? "sf-seat--committed"
                        : "sf-seat--proposed",
                );
            }
            const group = svgElement("g", {
                class: classes.join(" "),
                tabindex: "0",
                role: "img",
                "aria-label": seatDescription(seat),
                transform: `translate(${x} ${y})`,
            });
            if (seat.proposed) {
                group.style.setProperty("--party-color", partyColor(seat.proposed.party_key));
            }
            group.appendChild(svgElement("rect", {
                x: -13,
                y: -11,
                width: 26,
                height: 23,
                rx: 5,
                class: "sf-seat-shape",
            }));
            const label = svgElement("text", {
                x: 0,
                y: 4,
                class: "sf-seat-label",
            });
            label.textContent = seat.number;
            group.appendChild(label);
            if (seat.blocked || seat.unavailable) {
                const mark = svgElement("text", {
                    x: 0,
                    y: -15,
                    class: "sf-seat-mark",
                });
                mark.textContent = "×";
                group.appendChild(mark);
            } else if (seat.locked) {
                const mark = svgElement("text", {
                    x: 0,
                    y: -15,
                    class: "sf-seat-mark",
                });
                mark.textContent = "🔒";
                group.appendChild(mark);
            } else if (seat.accessible) {
                const mark = svgElement("text", {
                    x: 0,
                    y: -15,
                    class: "sf-seat-mark",
                });
                mark.textContent = "♿";
                group.appendChild(mark);
            }
            svg.appendChild(group);
        });
        container.replaceChildren(svg);
    }

    document.addEventListener("DOMContentLoaded", function () {
        const container = document.getElementById("solverforge-seat-map");
        const data = document.getElementById("solverforge-seat-map-data");
        if (!container || !data) {
            return;
        }
        try {
            renderSeatMap(container, JSON.parse(data.textContent));
        } catch (error) {
            container.textContent = `Could not render seat map: ${error.message}`;
            container.setAttribute("role", "alert");
        }
    });
}());
