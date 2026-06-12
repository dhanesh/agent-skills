import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { z } from 'astro:schema';

// B2 / RT-5: Extend the Starlight docs schema so every topic can declare
// when it was last reviewed and whether it offers decision guidance.
export const collections = {
  docs: defineCollection({
    loader: docsLoader(),
    schema: docsSchema({
      extend: z.object({
        last_reviewed: z.string().optional(),
        decision_guidance: z.boolean().optional(),
      }),
    }),
  }),
};
