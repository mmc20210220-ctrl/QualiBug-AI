interface DecisionCard {
  role: string;
  title: string;
  value: string;
  detail: string;
}

interface DecisionCardsProps {
  cards: DecisionCard[];
}

export function DecisionCards({ cards }: DecisionCardsProps) {
  if (!cards.length) return null;
  return (
    <div className="decision-cards">
      {cards.map((card) => (
        <article key={card.role} className="decision-card">
          <span className="decision-card-role">{card.role}</span>
          <h3>{card.title}</h3>
          <strong>{card.value}</strong>
          <p>{card.detail}</p>
        </article>
      ))}
    </div>
  );
}
