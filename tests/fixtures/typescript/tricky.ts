/**
 * Adds two numbers.
 */
export const add = (a: number, b: number): number => {
  return a + b;
};

export { helper } from "./helper";
export * from "./reexport";

class Widget {
  @Deco()
  method(): number {
    return this.compute();
  }

  private compute(): number {
    return add(1, 2);
  }
}
