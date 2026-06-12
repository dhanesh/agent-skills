import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { z } from 'astro:schema';

// B2 / RT-5: Extend the Starlight docs schema so every topic can declare
// when it was last reviewed, how fast it goes stale, and whether it offers
// decision guidance.
export const collections = {
  docs: defineCollection({
    loader: docsLoader(),
    schema: docsSchema({
      extend: z.object({
        // ISO date (YYYY-MM-DD) the topic was last reviewed. The freshness gate
        // compares this against the volatility tier's review window. Unquoted
        // YAML dates parse to a JS Date, so accept both Date and string and
        // normalise to an ISO string — authors needn't quote the date.
        last_reviewed: z
          .union([z.string(), z.date()])
          .transform((v) => (v instanceof Date ? v.toISOString().slice(0, 10) : v))
          .optional(),
        // Content freshness tier. 'volatile' (the default when unset) must be
        // reviewed within 12 months; 'stable' within 24 months.
        volatility: z.enum(['stable', 'volatile']).optional(),
        decision_guidance: z.boolean().optional(),
      }),
    }),
  }),
};
