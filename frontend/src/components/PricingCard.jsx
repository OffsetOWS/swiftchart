import { Check } from "lucide-react";

export default function PricingCard({ name, price, description, features, featured }) {
  return (
    <article className={`pricing-card ${featured ? "featured" : ""}`}>
      <div>
        <span className="plan-name">{name}</span>
        <h3>{price}</h3>
        <p>{description}</p>
      </div>
      <ul>
        {features.map((feature) => (
          <li key={feature}><Check size={17} /> {feature}</li>
        ))}
      </ul>
      <a className={`button ${featured ? "primary-button" : "secondary-button"}`} href="#telegram">
        {featured ? "Start Pro" : "Get Access"}
      </a>
    </article>
  );
}
