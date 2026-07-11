# `site_type` — operational definitions

The `site_type` axis is a **closed enum of 18 values**. Each is defined by the
**primary function of the homepage**, so that two independent annotators can
agree. `unknown` is *not* an enum value — it is the absence of a verdict,
represented by `site_type: null` plus `flags` that explain why.

| Value | Definition | Discriminator |
|---|---|---|
| `news_outlet` | Publishes current-affairs news at least daily, with an editorial process. | Publication dates on articles; a continuous flow. |
| `magazine` | Editorial content not tied to breaking news (lifestyle, reviews, culture). | Non-daily cadence; evergreen pieces. |
| `blog_personal` | Written by an individual or small group in a personal capacity. | No newsroom; first person; no company behind it. |
| `corporate` | A company's institutional site that does **not** sell online here. | "About", "Contact"; products described but not purchasable. |
| `ecommerce` | Sells its own products directly. | Cart + checkout + prices. |
| `marketplace` | Hosts third-party sellers. | Multiple sellers; seller pages. |
| `forum_community` | User-generated content in threads. | Threads, replies, user profiles. |
| `social_platform` | A social publishing platform. | Feed, follow, profiles. |
| `government` | A public body, at any level. | Official TLD/domain; administrative content. |
| `education` | A school, university, or course provider. | Programs, admissions, enrollment. |
| `academic_research` | Journals, preprints, research groups, datasets. | Papers, DOIs, affiliations. |
| `reference_wiki` | Encyclopedic knowledge or reference documentation. | Entries, lemma structure, little breaking news. |
| `saas_product` | Software sold on subscription. | Pricing tiers, "sign up", "docs", "API". |
| `portfolio` | Showcase of a professional's or studio's work. | Projects, "hire me"; no dominant blog. |
| `directory_aggregator` | Lists or aggregates others' content. | Dominant outbound links; little own content. |
| `media_streaming` | Distributes audio/video/music. | Player, catalog. |
| `gambling_adult` | Betting, casino, or sexually explicit content. | (The matching flag is also set.) |
| `other` | Alive and classifiable, but none of the above. | Use sparingly. |

If more than ~5% of a golden set lands in `other`, the enum needs extending.
