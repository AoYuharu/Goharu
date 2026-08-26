declare module "marked-terminal" {
  import type { MarkedExtension } from "marked";
  export function markedTerminal(options?: {
    width?: number;
    [key: string]: unknown;
  }): MarkedExtension;
  export default markedTerminal;
}
